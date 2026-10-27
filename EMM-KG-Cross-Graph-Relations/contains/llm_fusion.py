import os
"""
LLM融合 - 智能候选筛选版
先用规则筛选Top-10候选，再让LLM精准判断
"""

import json
import requests
import re
from neo4j import GraphDatabase
from datetime import datetime

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = "testcypher1.db"

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
MODEL_NAME = "qwen-plus"

BATCH_SIZE = 10


def get_candidates_by_similarity(driver, entity_name):
    """通过名称相似度快速筛选候选"""
    with driver.session(database=NEO4J_DATABASE) as session:
        candidates = []

        # 1. 字符串包含匹配
        name_lower = entity_name.lower()[:50]
        result = session.run("""
            MATCH (n) WHERE n:Term OR n:Other OR n:Classification
            WITH n, toLower(n.name) as lower_name
            WHERE lower_name CONTAINS $name OR $name CONTAINS lower_name
            RETURN n.name as name, labels(n) as labels
            LIMIT 20
        """, name=name_lower)
        candidates = [dict(r) for r in result]

        # 2. 如果不够，按实体类型补充
        if len(candidates) < 10:
            result = session.run("""
                MATCH (c:Classification) 
                RETURN c.name as name, ['Classification'] as labels 
                LIMIT 10
            """)
            candidates += [dict(r) for r in result]

        return candidates[:15]


def call_qwen_judge(entity_name, candidates):
    """让LLM从精确候选列表中判断关系"""
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json"
    }

    candidate_list = "\n".join([
        f"{i+1}. {c['name']} (type: {c['labels'][0]})"
        for i, c in enumerate(candidates)
    ])

    prompt = f"""Materials science expert. Classify relationship between:

Entity: "{entity_name}" (from materials science literature)

Candidate entities from knowledge base:
{candidate_list}

For EACH candidate, output ONE line in format:
number. relationship_type (confidence)

Where relationship_type is one of:
- isSameAs: same concept (TiO2 = titanium dioxide)
- belongsTo: entity is instance of category
- contains: one contains element of other  
- none: no relationship

Example output:
1. isSameAs (0.95)
2. contains (0.80)
3. none

Output ONLY numbered lines, nothing else:"""

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You are a materials science expert. Output ONLY in the specified format."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "max_tokens": 500
    }

    try:
        response = requests.post(DASHSCOPE_URL, headers=headers, json=payload, timeout=30)
        if response.status_code == 200:
            result = response.json()
            content = result['choices'][0]['message']['content'].strip()

            # 解析结果
            matches = []
            for line in content.split('\n'):
                line = line.strip()
                match = re.match(r'(\d+)[.\s]+(\w+)\s*\(?(\d*\.?\d*)?\)?', line)
                if match:
                    candidate_id = int(match.group(1))
                    rel_type = match.group(2).lower()
                    confidence = float(match.group(3)) if match.group(3) else 0.8

                    if rel_type in ['issameas', 'sameas', 'same']:
                        rel_type = 'isSameAs'
                    elif rel_type in ['belongsto', 'belongs']:
                        rel_type = 'belongsTo'
                    elif rel_type == 'contains':
                        rel_type = 'contains'
                    elif rel_type == 'none':
                        continue

                    if 1 <= candidate_id <= len(candidates):
                        matches.append({
                            'relationship': rel_type,
                            'node_name': candidates[candidate_id-1]['name'],
                            'confidence': confidence
                        })

            return matches
        else:
            return []
    except Exception as e:
        print(f"    异常: {e}")
        return []


def create_relationship(driver, entity_name, candidate_name, rel_type, confidence):
    """创建关系"""
    params = {"entity": entity_name, "candidate": candidate_name, "conf": min(confidence, 1.0)}

    with driver.session(database=NEO4J_DATABASE) as session:
        try:
            if rel_type == 'isSameAs':
                session.run("""
                    MATCH (e:Entity {name: $entity})
                    MATCH (n {name: $candidate}) WHERE n:Term OR n:Other
                    MERGE (e)-[r:isSameAs]->(n)
                    SET r.method='llm_smart', r.confidence=$conf, r.model='qwen', r.created_at=datetime()
                """, **params)
            elif rel_type == 'belongsTo':
                session.run("""
                    MATCH (e:Entity {name: $entity})
                    MATCH (c:Classification {name: $candidate})
                    MERGE (e)-[r:belongsTo]->(c)
                    SET r.method='llm_smart', r.confidence=$conf, r.model='qwen', r.created_at=datetime()
                """, **params)
            elif rel_type == 'contains':
                session.run("""
                    MATCH (e:Entity {name: $entity})
                    MATCH (n {name: $candidate}) WHERE n:Term OR n:Other
                    MERGE (e)-[r:contains]->(n)
                    SET r.method='llm_smart', r.confidence=$conf, r.model='qwen', r.created_at=datetime()
                """, **params)
            return 1
        except:
            return 0


def main():
    print("=" * 60)
    print("LLM智能融合 - 相似度筛选+LLM判断")
    print("=" * 60)

    if DASHSCOPE_API_KEY == "YOUR_DASHSCOPE_API_KEY":
        print("\n请先设置 API Key！")
        return

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    stats = {'isSameAs': 0, 'belongsTo': 0, 'contains': 0}

    try:
        for round_num in range(1, 11):  # 10轮
            print(f"\n第{round_num}轮...")

            with driver.session(database=NEO4J_DATABASE) as session:
                result = session.run("""
                    MATCH (e:Entity)
                    WHERE NOT EXISTS((e)-[:isSameAs|belongsTo|contains]->())
                      AND NOT EXISTS(()-[:isSameAs|belongsTo|contains]->(e))
                      AND e.cleanName IS NOT NULL
                    RETURN e.name as name, e.cleanName as cleanName
                    ORDER BY rand()
                    LIMIT $limit
                """, limit=BATCH_SIZE)
                entities = [dict(r) for r in result]

            print(f"Entity: {len(entities)}")

            for i, entity in enumerate(entities):
                entity_name = entity['cleanName'] or entity['name']

                # 智能筛选候选
                candidates = get_candidates_by_similarity(driver, entity_name)

                if len(candidates) > 0:
                    matches = call_qwen_judge(entity_name, candidates)

                    for match in matches:
                        created = create_relationship(
                            driver, entity['name'],
                            match['node_name'],
                            match['relationship'],
                            match.get('confidence', 0.8)
                        )
                        stats[match['relationship']] += created

                if (i+1) % 5 == 0:
                    print(f"  进度: {i+1}/{len(entities)}, 创建: {sum(stats.values())}")

        total = sum(stats.values())
        print(f"\n{'='*60}")
        print(f"LLM融合完成！总创建: {total} 条边")
        for k, v in stats.items():
            print(f"  {k}: {v}")
        print(f"{'='*60}")

    finally:
        driver.close()


if __name__ == "__main__":
    main()