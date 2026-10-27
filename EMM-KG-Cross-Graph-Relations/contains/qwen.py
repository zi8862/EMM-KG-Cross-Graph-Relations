import os
"""
LLM验证版融合 - 全量处理版
处理所有未匹配实体，分多轮执行
"""

import re
import requests
from neo4j import GraphDatabase
from datetime import datetime

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = "testcypher1.db"

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
MODEL_NAME = "qwen-turbo"

VALID_ELEMENTS = {'H','He','Li','Be','B','C','N','O','F','Ne','Na','Mg','Al','Si','P','S','Cl','Ar','K','Ca','Sc','Ti','V','Cr','Mn','Fe','Co','Ni','Cu','Zn','Ga','Ge','As','Se','Br','Kr','Rb','Sr','Y','Zr','Nb','Mo','Tc','Ru','Rh','Pd','Ag','Cd','In','Sn','Sb','Te','I','Xe','Cs','Ba','La','Ce','Pr','Nd','Sm','Eu','Gd','Tb','Dy','Ho','Er','Tm','Yb','Lu','Hf','Ta','W','Re','Os','Ir','Pt','Au','Hg','Tl','Pb','Bi','Th','Pa','U','Pu'}

# 每轮处理数量
BATCH_SIZE = 500
TOTAL_ROUNDS = 80  # 80轮 × 500 = 40,000个Entity


def get_unmatched_entities(driver, limit=500):
    """获取未匹配Entity"""
    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run("""
            MATCH (e:Entity)
            WHERE NOT EXISTS((e)-[:isSameAs|belongsTo|contains]->())
              AND NOT EXISTS(()-[:isSameAs|belongsTo|contains]->(e))
              AND e.cleanName IS NOT NULL AND size(e.cleanName) >= 2
            RETURN e.name as name, e.cleanName as cleanName
            ORDER BY rand()
            LIMIT $limit
        """, limit=limit)
        return [dict(r) for r in result]


def preload_mged_nodes(driver):
    """预加载MGED节点"""
    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run("""
            MATCH (n) WHERE n:Term OR n:Other OR n:Classification
            RETURN n.name as name, labels(n)[0] as type
        """)
        return [dict(r) for r in result]


def get_candidates(entity_name, all_nodes, max_cand=10):
    """智能候选筛选"""
    name_lower = entity_name.lower()
    candidates = []

    for node in all_nodes:
        node_name = node.get('name', '')
        if not node_name or len(node_name) < 2:
            continue
        node_lower = node_name.lower()

        # 精确子串匹配
        if name_lower in node_lower or node_lower in name_lower:
            len_ratio = len(name_lower) / max(len(node_lower), 1)
            if 0.25 <= len_ratio <= 4.0:  # 放宽长度比
                candidates.append(node)
                if len(candidates) >= max_cand:
                    break

    return candidates


def rule_validate(entity_name, candidate_name, rel_type):
    """规则二次验证"""
    e_lower = entity_name.lower()
    c_lower = candidate_name.lower()

    if rel_type == 'isSameAs':
        # 排除泛化词
        generic_words = {'concentration', 'density', 'efficiency', 'process',
                        'composite', 'shape', 'pattern', 'force', 'method',
                        'property', 'performance', 'material', 'system',
                        'size', 'rate', 'time', 'temperature'}
        if c_lower in generic_words:
            return False
        # 长度比限制
        len_ratio = len(e_lower) / max(len(c_lower), 1)
        if len_ratio < 0.3 or len_ratio > 3.0:
            return False
        # 至少2个字符相同
        if len(c_lower) < 3 and not c_lower.isalpha():
            return False

    if rel_type == 'contains':
        # 候选名包含有效化学元素或本身就是化学式
        elements_found = re.findall(r'[A-Z][a-z]?', candidate_name)
        valid_count = sum(1 for e in elements_found if e in VALID_ELEMENTS)
        if valid_count == 0 and candidate_name not in VALID_ELEMENTS:
            return False

    if rel_type == 'belongsTo':
        # 名称长度比不能差太多
        if abs(len(e_lower) - len(c_lower)) > max(len(e_lower), len(c_lower)) * 0.7:
            return False

    return True


def call_qwen(entity_name, candidates):
    """调用LLM"""
    candidate_list = "\n".join([
        f"{i+1}. {c['name']}"
        for i, c in enumerate(candidates[:10])
    ])

    prompt = f"""Entity: "{entity_name}"

Related nodes:
{candidate_list}

Output matching nodes (if clearly related):
number. relation

Relation types:
- same: names refer to same thing
- contains: entity contains this element/compound
- category: entity belongs to this material category

Be conservative. Output "0" if unsure.
Output:"""

    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "Materials expert. Be conservative. Output only format specified."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.0,
        "max_tokens": 150
    }

    try:
        resp = requests.post(DASHSCOPE_URL, headers=headers, json=payload, timeout=15)
        if resp.status_code == 200:
            content = resp.json()['choices'][0]['message']['content'].strip()
            if content == '0':
                return []

            matches = []
            for line in content.split('\n'):
                m = re.match(r'(\d+)[.\s]+(\w+)', line.strip())
                if m:
                    idx = int(m.group(1)) - 1
                    rel = m.group(2).lower()
                    if 0 <= idx < len(candidates):
                        if rel == 'same':
                            matches.append(('isSameAs', candidates[idx]))
                        elif rel == 'contains':
                            matches.append(('contains', candidates[idx]))
                        elif rel == 'category':
                            matches.append(('belongsTo', candidates[idx]))
            return matches
    except:
        pass
    return []


def create_edges(driver, entity_name, matches):
    """创建边"""
    created = 0
    with driver.session(database=NEO4J_DATABASE) as session:
        for rel_type, candidate in matches:
            params = {"e": entity_name, "c": candidate['name']}
            try:
                if rel_type == 'isSameAs' and candidate['type'] in ['Term', 'Other']:
                    session.run("""
                        MATCH (e:Entity {name: $e})
                        MATCH (n {name: $c}) WHERE n:Term OR n:Other
                        MERGE (e)-[r:isSameAs]->(n)
                        SET r.method='llm_verified', r.confidence=0.88, r.model='qwen-turbo', r.created_at=datetime()
                    """, **params)
                    created += 1
                elif rel_type == 'belongsTo' and candidate['type'] == 'Classification':
                    session.run("""
                        MATCH (e:Entity {name: $e})
                        MATCH (c:Classification {name: $c})
                        MERGE (e)-[r:belongsTo]->(c)
                        SET r.method='llm_verified', r.confidence=0.88, r.model='qwen-turbo', r.created_at=datetime()
                    """, **params)
                    created += 1
                elif rel_type == 'contains' and candidate['type'] in ['Term', 'Other']:
                    session.run("""
                        MATCH (e:Entity {name: $e})
                        MATCH (n {name: $c}) WHERE n:Term OR n:Other
                        MERGE (e)-[r:contains]->(n)
                        SET r.method='llm_verified', r.confidence=0.88, r.model='qwen-turbo', r.created_at=datetime()
                    """, **params)
                    created += 1
            except:
                pass
    return created


def main():
    print("=" * 60)
    print("LLM全量融合 - 80轮 × 500个Entity")
    print("=" * 60)

    if DASHSCOPE_API_KEY == "YOUR_DASHSCOPE_API_KEY":
        print("\n请设置API Key")
        return

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    stats = {'isSameAs': 0, 'belongsTo': 0, 'contains': 0}

    try:
        # 预加载节点
        all_nodes = preload_mged_nodes(driver)
        print(f"MGED节点: {len(all_nodes)}")

        # 初始未匹配数
        with driver.session(database=NEO4J_DATABASE) as session:
            result = session.run("""
                MATCH (e:Entity)
                WHERE NOT EXISTS((e)-[:isSameAs|belongsTo|contains]->())
                  AND NOT EXISTS(()-[:isSameAs|belongsTo|contains]->(e))
                RETURN count(e) as c
            """)
            print(f"初始未匹配: {result.single()['c']}")

        # 多轮处理
        for round_num in range(1, TOTAL_ROUNDS + 1):
            entities = get_unmatched_entities(driver, BATCH_SIZE)
            if not entities:
                print(f"\n第{round_num}轮: 无更多未匹配实体")
                break

            round_stats = {'isSameAs': 0, 'belongsTo': 0, 'contains': 0}

            for entity in entities:
                entity_name = entity['cleanName'] or entity['name']
                candidates = get_candidates(entity_name, all_nodes, 10)

                if candidates:
                    matches = call_qwen(entity_name, candidates)
                    for rel_type, candidate in matches:
                        if rule_validate(entity_name, candidate['name'], rel_type):
                            created = create_edges(driver, entity_name, [(rel_type, candidate)])
                            round_stats[rel_type] += created
                            stats[rel_type] += created

            total_round = sum(round_stats.values())
            total_all = sum(stats.values())
            if round_num % 10 == 0:
                print(f"  第{round_num}轮: 处理{len(entities)}个, "
                      f"本轮创建{total_round}, 累计{total_all}")

        total = sum(stats.values())
        print(f"\n{'='*60}")
        print(f"全量融合完成！总创建: {total} 条边")
        print(f"  isSameAs: {stats['isSameAs']}")
        print(f"  belongsTo: {stats['belongsTo']}")
        print(f"  contains: {stats['contains']}")
        print(f"{'='*60}")

    finally:
        driver.close()


if __name__ == "__main__":
    main()