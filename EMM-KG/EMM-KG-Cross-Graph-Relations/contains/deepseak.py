import os
"""
LLM融合 - DeepSeek版（永久免费）
API获取: https://platform.deepseek.com/
"""

import re
import requests
import time
from neo4j import GraphDatabase
from datetime import datetime

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = "testcypher1.db"

# ===== DeepSeek API配置 =====
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL_NAME = "deepseek-chat"

BATCH_SIZE = 50
TOTAL_ROUNDS = 100

VALID_ELEMENTS = {'H', 'He', 'Li', 'Be', 'B', 'C', 'N', 'O', 'F', 'Ne', 'Na', 'Mg', 'Al', 'Si', 'P', 'S', 'Cl', 'Ar',
                  'K', 'Ca', 'Sc', 'Ti', 'V', 'Cr', 'Mn', 'Fe', 'Co', 'Ni', 'Cu', 'Zn', 'Ga', 'Ge', 'As', 'Se', 'Br',
                  'Rb', 'Sr', 'Y', 'Zr', 'Nb', 'Mo', 'Ru', 'Rh', 'Pd', 'Ag', 'Cd', 'In', 'Sn', 'Sb', 'Te', 'I', 'Cs',
                  'Ba', 'La', 'Ce', 'Nd', 'Sm', 'Eu', 'Gd', 'Tb', 'Dy', 'Ho', 'Er', 'Tm', 'Yb', 'Lu', 'Hf', 'Ta', 'W',
                  'Re', 'Os', 'Ir', 'Pt', 'Au', 'Hg', 'Tl', 'Pb', 'Bi', 'Th', 'U'}


def get_unmatched(driver, limit=50):
    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run("""
            MATCH (e:Entity)
            WHERE NOT EXISTS((e)-[:isSameAs|belongsTo|contains]->())
              AND NOT EXISTS(()-[:isSameAs|belongsTo|contains]->(e))
              AND e.cleanName IS NOT NULL AND size(e.cleanName) >= 3
            RETURN e.name as name, e.cleanName as cleanName
            ORDER BY rand()
            LIMIT $limit
        """, limit=limit)
        return [dict(r) for r in result]


def load_nodes(driver):
    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run("""
            MATCH (n) WHERE n:Term OR n:Other OR n:Classification
            RETURN n.name as name, labels(n)[0] as type
        """)
        return [dict(r) for r in result]


def get_candidates(entity_name, all_nodes, max_cand=8):
    name_lower = entity_name.lower()[:30]
    candidates = []
    for node in all_nodes:
        node_name = node.get('name', '')
        if not node_name or len(node_name) < 2:
            continue
        node_lower = node_name.lower()
        if len(name_lower) >= 3 and len(node_lower) >= 3:
            if name_lower in node_lower or node_lower in name_lower:
                len_ratio = len(name_lower) / max(len(node_lower), 1)
                if 0.2 <= len_ratio <= 5.0:
                    candidates.append(node)
                    if len(candidates) >= max_cand:
                        break
    return candidates[:max_cand]


def call_deepseek(prompt):
    """调用DeepSeek API（带重试）"""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "Materials science expert. Output only specified format. Be concise."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.0,
        "max_tokens": 100
    }

    for attempt in range(3):
        try:
            resp = requests.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=15)
            if resp.status_code == 200:
                return resp.json()['choices'][0]['message']['content'].strip()
            elif resp.status_code == 429:
                print(f"    限流，等待3秒...")
                time.sleep(3)
            else:
                time.sleep(1)
        except Exception as e:
            time.sleep(1)
    return "0"


def parse_response(content, candidates):
    if not content or content == '0':
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


def validate(entity_name, candidate_name, rel_type):
    e = entity_name.lower()
    c = candidate_name.lower()
    if rel_type == 'isSameAs':
        bad = {'concentration', 'density', 'efficiency', 'process', 'composite',
               'shape', 'pattern', 'force', 'method', 'property', 'material', 'system'}
        if c in bad or e in bad:
            return False
        if abs(len(e) - len(c)) > max(len(e), len(c)) * 0.6:
            return False
    if rel_type == 'contains':
        elems = re.findall(r'[A-Z][a-z]?', candidate_name)
        if not any(x in VALID_ELEMENTS for x in elems):
            return False
    return True


def create_edge(driver, entity_name, candidate_name, rel_type, candidate_type):
    params = {"e": entity_name, "c": candidate_name}
    try:
        with driver.session(database=NEO4J_DATABASE) as session:
            if rel_type == 'isSameAs' and candidate_type in ['Term', 'Other']:
                session.run("""
                    MATCH (e:Entity {name: $e})
                    MATCH (n {name: $c}) WHERE n:Term OR n:Other
                    MERGE (e)-[r:isSameAs]->(n)
                    SET r.method='llm_deepseek', r.confidence=0.9, r.created_at=datetime()
                """, **params)
                return 1
            elif rel_type == 'belongsTo' and candidate_type == 'Classification':
                session.run("""
                    MATCH (e:Entity {name: $e})
                    MATCH (c:Classification {name: $c})
                    MERGE (e)-[r:belongsTo]->(c)
                    SET r.method='llm_deepseek', r.confidence=0.9, r.created_at=datetime()
                """, **params)
                return 1
            elif rel_type == 'contains' and candidate_type in ['Term', 'Other']:
                session.run("""
                    MATCH (e:Entity {name: $e})
                    MATCH (n {name: $c}) WHERE n:Term OR n:Other
                    MERGE (e)-[r:contains]->(n)
                    SET r.method='llm_deepseek', r.confidence=0.9, r.created_at=datetime()
                """, **params)
                return 1
    except:
        pass
    return 0


def main():
    print("=" * 50)
    print("LLM融合 - DeepSeek版（永久免费）")
    print("=" * 50)

    if DEEPSEEK_API_KEY == "YOUR_DEEPSEEK_API_KEY":
        print("\n请先获取API Key: https://platform.deepseek.com/")
        return

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    stats = {'isSameAs': 0, 'belongsTo': 0, 'contains': 0}

    try:
        all_nodes = load_nodes(driver)
        print(f"加载节点: {len(all_nodes)}")

        for rnd in range(1, TOTAL_ROUNDS + 1):
            entities = get_unmatched(driver, BATCH_SIZE)
            if not entities:
                print(f"第{rnd}轮: 无更多未匹配实体")
                break

            created_this_round = 0

            for i, entity in enumerate(entities):
                name = entity['cleanName'] or entity['name']
                cands = get_candidates(name, all_nodes, 8)

                if cands:
                    prompt = f"Entity: \"{name}\"\nCandidates:\n" + \
                             "\n".join(f"{j + 1}. {c['name']}" for j, c in enumerate(cands)) + \
                             "\nOutput (number. same/contains/category) or 0:"

                    content = call_deepseek(prompt)
                    matches = parse_response(content, cands)

                    for rel_type, cand in matches:
                        if validate(name, cand['name'], rel_type):
                            ok = create_edge(driver, entity['name'], cand['name'],
                                             rel_type, cand['type'])
                            stats[rel_type] += ok
                            created_this_round += ok

                if (i + 1) % 10 == 0:
                    time.sleep(0.3)

            total = sum(stats.values())
            if rnd % 10 == 0:
                print(f"  第{rnd}轮: 处理{len(entities)}个, "
                      f"本轮{created_this_round}, 累计{total}")

        total = sum(stats.values())
        print(f"\n完成! 总创建: {total}")
        print(f"  isSameAs:{stats['isSameAs']} belongsTo:{stats['belongsTo']} contains:{stats['contains']}")

    finally:
        driver.close()


if __name__ == "__main__":
    main()