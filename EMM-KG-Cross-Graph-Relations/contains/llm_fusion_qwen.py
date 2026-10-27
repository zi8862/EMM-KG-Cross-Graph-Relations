import os
"""
LLM大规模融合 - 优化版
覆盖所有未匹配实体，高质量匹配
"""

import re
import json
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from neo4j import GraphDatabase
from datetime import datetime

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = "testcypher1.db"

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
MODEL_NAME = "qwen-max"

MAX_WORKERS = 5  # 并发数
BATCH_SIZE = 100  # 每批处理Entity数
TOTAL_ROUNDS = 50  # 总轮数


def get_all_mged_nodes(driver):
    """一次性获取所有MGED节点名称列表"""
    with driver.session(database=NEO4J_DATABASE) as session:
        # 只获取Term/Other节点名称（用于isSameAs和contains）
        result = session.run("""
            MATCH (n) WHERE n:Term OR n:Other
            RETURN n.name as name, labels(n)[0] as type
        """)
        terms = [dict(r) for r in result]

        # 获取Classification节点名称（用于belongsTo）
        result = session.run("""
            MATCH (c:Classification)
            RETURN c.name as name, 'Classification' as type
        """)
        classifications = [dict(r) for r in result]

        return terms, classifications


def get_unmatched_entities(driver, limit=100):
    """获取未匹配的Entity"""
    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run("""
            MATCH (e:Entity)
            WHERE NOT EXISTS((e)-[:isSameAs|belongsTo|contains]->())
              AND NOT EXISTS(()-[:isSameAs|belongsTo|contains]->(e))
              AND e.cleanName IS NOT NULL
              AND e.cleanName <> ''
              AND size(e.cleanName) >= 2
            RETURN e.name as name, e.cleanName as cleanName
            ORDER BY rand()
            LIMIT $limit
        """, limit=limit)
        return [dict(r) for r in result]


def find_candidates_by_substring(entity_name, all_nodes, max_candidates=10):
    """通过子串匹配快速筛选候选"""
    name_lower = entity_name.lower()
    candidates = []

    for node in all_nodes:
        node_name = node.get('name')
        if not node_name or not isinstance(node_name, str):
            continue
        node_name_lower = node_name.lower()
        if len(node_name_lower) < 2:
            continue
        # 互相包含且长度至少2
        if len(name_lower) >= 2 and len(node_name_lower) >= 2:
            if name_lower in node_name_lower or node_name_lower in name_lower:
                candidates.append(node)
                if len(candidates) >= max_candidates:
                    break

    return candidates


def construct_prompt(entity_name, candidates, candidate_type="terms"):
    """构造优化的提示词"""
    # 过滤空名称
    valid_candidates = [c for c in candidates if c.get('name') and c['name'].strip()]

    candidate_list = "\n".join([
        f"{i + 1}. {c['name']}"
        for i, c in enumerate(valid_candidates[:15])
    ])

    if not candidate_list:
        return None

    if candidate_type == "classification":
        prompt = f"""Materials science expert. Entity: "{entity_name}"

Which of these material CATEGORIES does this entity belong to?
{candidate_list}

Output ONLY numbers of matching categories (e.g., "1,5,7"). If none, output "0". Output:"""
    else:
        prompt = f"""Materials science expert. Entity: "{entity_name}"

Which of these terms are RELATED to this entity?
{candidate_list}

Output each match as: number. relationship
Where relationship is one of:
- same: exactly the same concept
- contains: entity contains this as element/component

Example: 3. same
         7. contains

Output ONLY numbered lines. If none, output "0". Output:"""

    return prompt


def call_qwen(prompt, max_tokens=200):
    """调用通义千问API"""
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You are a materials science expert. Output ONLY in the specified format."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.0,
        "max_tokens": max_tokens
    }

    try:
        response = requests.post(DASHSCOPE_URL, headers=headers, json=payload, timeout=15)
        if response.status_code == 200:
            result = response.json()
            content = result['choices'][0]['message']['content'].strip()
            return content
        return None
    except:
        return None


def parse_matches(content, candidates, match_type="terms"):
    """解析LLM返回的匹配结果"""
    matches = []
    if not content or content == '0':
        return matches

    for line in content.split('\n'):
        line = line.strip()

        if match_type == "classification":
            # 解析 "1,5,7" 格式
            numbers = re.findall(r'\d+', line)
            for num in numbers:
                idx = int(num) - 1
                if 0 <= idx < len(candidates):
                    matches.append({
                        'node_name': candidates[idx]['name'],
                        'relationship': 'belongsTo',
                        'confidence': 0.85
                    })
        else:
            # 解析 "3. same" 或 "7. contains" 格式
            match = re.match(r'(\d+)[.\s]+(\w+)', line)
            if match:
                idx = int(match.group(1)) - 1
                rel = match.group(2).lower()
                if 0 <= idx < len(candidates):
                    if rel in ['same', 'issameas', 'equivalent']:
                        matches.append({
                            'node_name': candidates[idx]['name'],
                            'relationship': 'isSameAs',
                            'confidence': 0.85
                        })
                    elif rel in ['contains', 'contain', 'component']:
                        matches.append({
                            'node_name': candidates[idx]['name'],
                            'relationship': 'contains',
                            'confidence': 0.85
                        })

    return matches


def filter_invalid_matches(matches, entity_name):
    """过滤明显的错误匹配"""
    filtered = []
    entity_lower = entity_name.lower()

    for match in matches:
        node_name = match['node_name']
        node_lower = node_name.lower()

        # 过滤单字母匹配（除非是真正的化学元素）
        if len(node_name) == 1:
            # 只保留真正的化学元素
            valid_elements = {'H', 'B', 'C', 'N', 'O', 'F', 'P', 'S', 'K', 'V', 'Y', 'I', 'W', 'U'}
            if node_name not in valid_elements:
                continue

        # 过滤空字符串
        if not node_name or not node_name.strip():
            continue

        # 过滤纯数字匹配
        if node_name.isdigit():
            continue

        filtered.append(match)

    return filtered


def create_relationship_batch(driver, entity_name, matches):
    """批量创建关系"""
    created = 0
    with driver.session(database=NEO4J_DATABASE) as session:
        for match in matches:
            params = {
                "entity": entity_name,
                "candidate": match['node_name'],
                "conf": match['confidence']
            }
            try:
                if match['relationship'] == 'isSameAs':
                    result = session.run("""
                        MATCH (e:Entity {name: $entity})
                        MATCH (n {name: $candidate}) WHERE n:Term OR n:Other
                        MERGE (e)-[r:isSameAs]->(n)
                        SET r.method='llm_optimized', r.confidence=$conf, r.model='qwen', r.created_at=datetime()
                        RETURN count(r) as c
                    """, **params)
                elif match['relationship'] == 'belongsTo':
                    result = session.run("""
                        MATCH (e:Entity {name: $entity})
                        MATCH (c:Classification {name: $candidate})
                        MERGE (e)-[r:belongsTo]->(c)
                        SET r.method='llm_optimized', r.confidence=$conf, r.model='qwen', r.created_at=datetime()
                        RETURN count(r) as c
                    """, **params)
                elif match['relationship'] == 'contains':
                    result = session.run("""
                        MATCH (e:Entity {name: $entity})
                        MATCH (n {name: $candidate}) WHERE n:Term OR n:Other
                        MERGE (e)-[r:contains]->(n)
                        SET r.method='llm_optimized', r.confidence=$conf, r.model='qwen', r.created_at=datetime()
                        RETURN count(r) as c
                    """, **params)
                created += 1
            except:
                pass
    return created


def process_entity_batch(driver, entities, terms, classifications, stats):
    """处理一批实体"""
    for entity in entities:
        entity_name = entity['cleanName'] or entity['name']
        if not entity_name or len(entity_name.strip()) < 2:
            continue

        # 1. isSameAs/contains 匹配
        term_candidates = find_candidates_by_substring(entity_name, terms, 15)
        if term_candidates:
            prompt = construct_prompt(entity_name, term_candidates, "terms")
            if prompt:
                content = call_qwen(prompt, 200)
                if content:
                    matches = parse_matches(content, term_candidates, "terms")
                    matches = filter_invalid_matches(matches, entity_name)
                    for m in matches:
                        stats[m['relationship']] += create_relationship_batch(
                            driver, entity_name, [m]
                        )

        # 2. belongsTo 匹配
        class_candidates = find_candidates_by_substring(entity_name, classifications, 10)
        if class_candidates:
            prompt = construct_prompt(entity_name, class_candidates, "classification")
            if prompt:
                content = call_qwen(prompt, 100)
                if content:
                    matches = parse_matches(content, class_candidates, "classification")
                    matches = filter_invalid_matches(matches, entity_name)
                    for m in matches:
                        stats[m['relationship']] += create_relationship_batch(
                            driver, entity_name, [m]
                        )

    return stats


def main():
    print("=" * 60)
    print("LLM大规模融合 - 优化版")
    print("=" * 60)

    if DASHSCOPE_API_KEY == "YOUR_DASHSCOPE_API_KEY":
        print("\n请先设置 API Key！")
        return

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    stats = {'isSameAs': 0, 'belongsTo': 0, 'contains': 0}

    try:
        # 预加载所有MGED节点
        print("\n[1] 加载MGED节点...")
        terms, classifications = get_all_mged_nodes(driver)
        print(f"    Term/Other节点: {len(terms)}")
        print(f"    Classification节点: {len(classifications)}")

        # 初始未匹配数量
        with driver.session(database=NEO4J_DATABASE) as session:
            result = session.run("""
                MATCH (e:Entity)
                WHERE NOT EXISTS((e)-[:isSameAs|belongsTo|contains]->())
                  AND NOT EXISTS(()-[:isSameAs|belongsTo|contains]->(e))
                RETURN count(e) as count
            """)
            print(f"    初始未匹配Entity: {result.single()['count']}")

        # 批量处理
        print(f"\n[2] 开始大规模处理 ({TOTAL_ROUNDS}轮, 每轮{BATCH_SIZE}个Entity)...")

        total_processed = 0
        for round_num in range(1, TOTAL_ROUNDS + 1):
            # 获取未匹配实体
            entities = get_unmatched_entities(driver, BATCH_SIZE)
            if not entities:
                print(f"\n    第{round_num}轮: 无更多未匹配实体，提前结束")
                break

            # 处理这批实体
            process_entity_batch(driver, entities, terms, classifications, stats)

            total_processed += len(entities)
            total_created = sum(stats.values())

            if round_num % 5 == 0:
                print(f"    第{round_num}轮: 处理{len(entities)}个, "
                      f"累计处理{total_processed}个, 创建{total_created}条边 "
                      f"(isSameAs:{stats['isSameAs']}, belongsTo:{stats['belongsTo']}, contains:{stats['contains']})")

        # 最终统计
        total = sum(stats.values())
        print(f"\n{'=' * 60}")
        print(f"LLM大规模融合完成！")
        print(f"  总处理Entity: {total_processed}")
        print(f"  总创建边: {total}")
        print(f"  isSameAs: {stats['isSameAs']}")
        print(f"  belongsTo: {stats['belongsTo']}")
        print(f"  contains: {stats['contains']}")
        print(f"{'=' * 60}")

        # 创建实验记录
        with driver.session(database=NEO4J_DATABASE) as session:
            session.run("""
                MERGE (fm:FusionMethod {method_id: 'llm_optimized'})
                SET fm.relation_type = 'all',
                    fm.name = 'LLM大规模融合(优化版)',
                    fm.match_count = $count,
                    fm.quality_level = 'B',
                    fm.status = 'verified',
                    fm.updated_at = datetime()
            """, count=total)

    finally:
        driver.close()


if __name__ == "__main__":
    main()