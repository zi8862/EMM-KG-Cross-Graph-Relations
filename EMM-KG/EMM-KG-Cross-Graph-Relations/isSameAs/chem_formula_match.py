import os
"""
化学式标准化匹配脚本 - 修复版
"""

import re
from neo4j import GraphDatabase

# ============ 配置 ============
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = "testcypher1.db"


# ============ 化学式解析 ============
def normalize_chemical_formula(text):
    """标准化化学式"""
    if not text:
        return None
    text = re.sub(r'[\(\)\[\]\{\}\s]', '', text)
    text = text.strip()
    pattern = r'^([A-Z][a-z]?\d*)+$'
    if re.match(pattern, text):
        elements = re.findall(r'([A-Z][a-z]?)(\d*)', text)
        parsed = {}
        for elem, count in elements:
            count = int(count) if count else 1
            parsed[elem] = parsed.get(elem, 0) + count
        sorted_elements = sorted(parsed.items())
        normalized = ''.join(f"{elem}{count if count > 1 else ''}"
                             for elem, count in sorted_elements)
        return normalized
    return None


def extract_chemical_formula_fuzzy(text):
    """从文本中提取化学式"""
    if not text:
        return None, None
    patterns = [
        r'\b([A-Z][a-z]?\d*(?:[A-Z][a-z]?\d*)+)\b',
        r'\b([A-Z][a-z]?[0-9]?[A-Z][a-z]?[0-9]*)\b',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            formula = match.group(1)
            normalized = normalize_chemical_formula(formula)
            if normalized:
                return normalized, formula
    return None, None


class Neo4jMatcher:
    def __init__(self, uri, user, password, database):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database

    def close(self):
        self.driver.close()

    def get_unmatched_chm_entities(self):
        """获取未匹配的 CHM 实体 - 使用 name 作为唯一标识"""
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (e:Entity)
                WHERE e.entitySignificance CONTAINS 'CHM'
                  AND NOT EXISTS((e)-[:isSameAs]->())
                RETURN e.name as name, 
                       e.cleanName as cleanName
            """)
            return [dict(record) for record in result]

    def get_mged_term_by_name_pattern(self, pattern):
        """根据名称模式搜索 MGED 术语"""
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (n)
                WHERE (n:Term OR n:Other)
                  AND toLower(n.name) CONTAINS toLower($pattern)
                RETURN n.name as name, labels(n) as labels
                LIMIT 1
            """, pattern=pattern)
            record = result.single()
            return dict(record) if record else None

    def create_isSameAs_by_name(self, entity_name, mged_name, method, confidence, formula):
        """通过名称创建 isSameAs 关系"""
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (e:Entity {name: $entity_name})
                MATCH (m) WHERE (m:Term OR m:Other) AND m.name = $mged_name
                MERGE (e)-[r:isSameAs]->(m)
                SET r.method = $method,
                    r.confidence = $confidence,
                    r.chemical_formula = $formula,
                    r.created_at = datetime()
                RETURN count(r) as created
            """,
                                 entity_name=entity_name,
                                 mged_name=mged_name,
                                 method=method,
                                 confidence=confidence,
                                 formula=formula)
            record = result.single()
            return record['created'] if record else 0

    def create_fusion_method_record(self, method_id, name, description, confidence, count):
        """创建融合方法记录"""
        with self.driver.session(database=self.database) as session:
            session.run("""
                MERGE (fm:FusionMethod {method_id: $method_id})
                SET fm.name = $name,
                    fm.description = $description,
                    fm.confidence_threshold = $confidence,
                    fm.match_count = $count,
                    fm.entity_type = 'CHM',
                    fm.updated_at = datetime()
            """,
                        method_id=method_id,
                        name=name,
                        description=description,
                        confidence=confidence,
                        count=count)


def main():
    matcher = Neo4jMatcher(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE)

    print("=" * 60)
    print("化学式匹配开始（修复版）")
    print("=" * 60)

    print("\n[1/3] 获取未匹配的 CHM 实体...")
    chm_entities = matcher.get_unmatched_chm_entities()
    print(f"      获取到 {len(chm_entities)} 个未匹配的 CHM 实体")

    print("\n[2/3] 执行化学式匹配...")

    matches_exact = []
    matches_fuzzy = []

    for i, entity in enumerate(chm_entities):
        if (i + 1) % 1000 == 0:
            print(f"      处理进度: {i + 1}/{len(chm_entities)}")

        entity_name = entity['name']
        clean_name = entity['cleanName']

        # 提取化学式
        normalized = normalize_chemical_formula(clean_name)
        original = clean_name
        if not normalized:
            normalized, original = extract_chemical_formula_fuzzy(entity_name)

        if not normalized:
            continue

        # 直接用化学式搜索 MGED
        # 策略1: 精确化学式匹配
        mg_term = matcher.get_mged_term_by_name_pattern(original)
        if mg_term:
            matches_exact.append({
                'entity_name': entity_name,
                'mged_name': mg_term['name'],
                'formula': normalized,
                'original': original
            })
            continue

        # 策略2: 标准化化学式匹配
        mg_term = matcher.get_mged_term_by_name_pattern(normalized)
        if mg_term:
            matches_fuzzy.append({
                'entity_name': entity_name,
                'mged_name': mg_term['name'],
                'formula': normalized,
                'original': original
            })

    print(f"\n      匹配结果:")
    print(f"      - 精确化学式匹配: {len(matches_exact)}")
    print(f"      - 标准化化学式匹配: {len(matches_fuzzy)}")

    # 去重：同一 Entity 只保留第一个匹配
    seen_entities = set()
    final_matches = []
    for match in matches_exact:
        if match['entity_name'] not in seen_entities:
            seen_entities.add(match['entity_name'])
            final_matches.append((match, 'chem_formula_exact', 0.85))
    for match in matches_fuzzy:
        if match['entity_name'] not in seen_entities:
            seen_entities.add(match['entity_name'])
            final_matches.append((match, 'chem_formula_fuzzy', 0.75))

    print(f"      去重后匹配数: {len(final_matches)}")

    print("\n[3/3] 写入 Neo4j...")

    created_exact = 0
    created_fuzzy = 0

    for i, (match, method, conf) in enumerate(final_matches):
        if (i + 1) % 50 == 0:
            print(f"      写入进度: {i + 1}/{len(final_matches)}")

        result = matcher.create_isSameAs_by_name(
            match['entity_name'],
            match['mged_name'],
            method,
            conf,
            match['formula']
        )
        if result:
            if method == 'chem_formula_exact':
                created_exact += 1
            else:
                created_fuzzy += 1

    print(f"\n      成功创建 {created_exact + created_fuzzy} 条 isSameAs 边")
    print(f"      - chem_formula_exact: {created_exact}")
    print(f"      - chem_formula_fuzzy: {created_fuzzy}")

    # 创建方法记录
    matcher.create_fusion_method_record(
        'chem_formula_exact',
        '化学式精确匹配',
        '通过化学式直接匹配MGED术语',
        0.85,
        created_exact
    )
    matcher.create_fusion_method_record(
        'chem_formula_fuzzy',
        '化学式标准化匹配',
        '通过化学式标准化后匹配MGED术语',
        0.75,
        created_fuzzy
    )

    print("\n" + "=" * 60)
    print("化学式匹配完成！")
    print("=" * 60)

    matcher.close()


if __name__ == "__main__":
    main()