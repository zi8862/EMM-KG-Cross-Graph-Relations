import os
"""
belongsTo Layer 1 V2 - 严格化学式规则匹配（改进版）
修复：半导体规则过宽、碳同位素误匹配、增加已知化学品映射
"""

import re
from neo4j import GraphDatabase

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = "testcypher1.db"

# ============ 已知化学元素 ============
KNOWN_ELEMENTS = {
    'H', 'He', 'Li', 'Be', 'B', 'C', 'N', 'O', 'F', 'Ne',
    'Na', 'Mg', 'Al', 'Si', 'P', 'S', 'Cl', 'Ar', 'K', 'Ca',
    'Sc', 'Ti', 'V', 'Cr', 'Mn', 'Fe', 'Co', 'Ni', 'Cu', 'Zn',
    'Ga', 'Ge', 'As', 'Se', 'Br', 'Kr', 'Rb', 'Sr', 'Y', 'Zr',
    'Nb', 'Mo', 'Tc', 'Ru', 'Rh', 'Pd', 'Ag', 'Cd', 'In', 'Sn',
    'Sb', 'Te', 'I', 'Xe', 'Cs', 'Ba', 'La', 'Ce', 'Pr', 'Nd',
    'Pm', 'Sm', 'Eu', 'Gd', 'Tb', 'Dy', 'Ho', 'Er', 'Tm', 'Yb',
    'Lu', 'Hf', 'Ta', 'W', 'Re', 'Os', 'Ir', 'Pt', 'Au', 'Hg',
    'Tl', 'Pb', 'Bi', 'Po', 'At', 'Rn', 'Fr', 'Ra', 'Ac', 'Th',
    'Pa', 'U', 'Np', 'Pu', 'Am', 'Cm'
}

# 非元素缩写（有机化学中常见的缩写，会被误识别为元素）
NON_ELEMENT_PATTERNS = {
    'Ac', 'Me', 'Et', 'Pr', 'Bu', 'Ph', 'Su', 'Pl', 'Di', 'Tr',
    'Ep', 'Gl', 'To', 'Be'
}

# ============ 已知化学品精确映射 ============
# 从名称中提取的纯化学式 -> (Classification关键词, 置信度)
KNOWN_CHEMICALS = {
    # 氧化物 → 陶瓷/耐火材料
    'TiO2': ('Oxide_ceramics', 0.95),
    'Al2O3': ('Oxide_ceramics', 0.95),
    'SiO2': ('Oxide_ceramics', 0.95),
    'MgO': ('Oxide_ceramics', 0.90),
    'CaO': ('Oxide_ceramics', 0.90),
    'Fe2O3': ('Oxide_ceramics', 0.90),
    'Fe3O4': ('Oxide_ceramics', 0.90),
    'CuO': ('Oxide_ceramics', 0.90),
    'ZnO': ('Oxide_ceramics', 0.90),
    'ZrO2': ('Oxide_ceramics', 0.95),
    'Y2O3': ('Oxide_ceramics', 0.90),
    'CeO2': ('Oxide_ceramics', 0.90),
    'AlO': ('Oxide_ceramics', 0.85),
    'AlO2': ('Oxide_ceramics', 0.85),
    'SiO': ('Oxide_ceramics', 0.85),
    'TiO': ('Oxide_ceramics', 0.85),

    # 半导体
    'GaAs': ('Semiconductor_Materials', 0.95),
    'GaN': ('Semiconductor_Materials', 0.95),
    'InP': ('Semiconductor_Materials', 0.95),
    'InAs': ('Semiconductor_Materials', 0.95),
    'GaP': ('Semiconductor_Materials', 0.95),
    'InN': ('Semiconductor_Materials', 0.90),
    'CdTe': ('Semiconductor_Materials', 0.95),
    'CdSe': ('Semiconductor_Materials', 0.90),
    'ZnSe': ('Semiconductor_Materials', 0.90),
    'ZnTe': ('Semiconductor_Materials', 0.90),
    'MoS2': ('Semiconductor_Materials', 0.85),
    'MoSe2': ('Semiconductor_Materials', 0.85),
    'WS2': ('Semiconductor_Materials', 0.85),

    # 电池材料
    'LiCoO2': ('Battery_Materials', 0.95),
    'LiMn2O4': ('Battery_Materials', 0.95),
    'LiFePO4': ('Battery_Materials', 0.95),
    'LiNiO2': ('Battery_Materials', 0.90),
    'LiCoO': ('Battery_Materials', 0.85),
    'LiMnO': ('Battery_Materials', 0.85),

    # 钙钛矿
    'BaTiO3': ('Perovskite_materials', 0.95),
    'SrTiO3': ('Perovskite_materials', 0.95),
    'CaTiO3': ('Perovskite_materials', 0.90),

    # 碳化物
    'SiC': ('Carbide_ceramics', 0.90),
    'WC': ('Carbide_ceramics', 0.90),
    'TiC': ('Carbide_ceramics', 0.85),

    # 氮化物
    'BN': ('Nitride_ceramics', 0.85),
    'AlN': ('Nitride_ceramics', 0.85),
    'Si3N4': ('Nitride_ceramics', 0.90),
}

# ============ 规则库 ============
# (规则名, Classification关键词, 必须全含的元素集合, 最少额外元素数, 最多元素数, 排除的名称正则, 置信度)
RULES = [
    # === 氧化物（必须含O + 至少一个金属元素） ===
    ('oxide', 'Oxide_ceramics', {'O'}, 1, 5, None, 0.92),
    ('oxide_simple', 'Refractory_Materials', {'O'}, 1, 3, None, 0.85),

    # === 单一金属/合金 ===
    ('steel', 'Steels', {'Fe'}, 0, 3, None, 0.92),
    ('aluminum', 'Aluminum', {'Al'}, 0, 2, None, 0.90),
    ('copper', 'Copper', {'Cu'}, 0, 2, None, 0.90),
    ('titanium', 'Titanium', {'Ti'}, 0, 2, None, 0.90),
    ('nickel', 'Nickel', {'Ni'}, 0, 2, None, 0.88),
    ('magnesium', 'Magnesium_and_magnesium_alloys', {'Mg'}, 0, 2, None, 0.88),
    ('zinc', 'Zinc', {'Zn'}, 0, 2, None, 0.88),
    ('tungsten', 'Tungsten', {'W'}, 0, 2, None, 0.85),
    ('cobalt', 'Cobalt', {'Co'}, 0, 2, None, 0.85),
    ('gold', 'Gold', {'Au'}, 0, 1, None, 0.88),
    ('silver', 'Silver', {'Ag'}, 0, 1, None, 0.88),
    ('platinum', 'Platinum', {'Pt'}, 0, 1, None, 0.88),

    # === 多金属合金（含3种以上金属元素） ===
    ('multi_alloy', 'Alloy_steels', set(), 3, 5, None, 0.75),

    # === 碳材料（纯碳，排除含大量H或O的） ===
    ('carbon_pure', 'Carbon_Materials', {'C'}, 0, 1,
     r'(?i)acid|alcohol|acetone|methane|ethane|benzene|glucose|sucrose|phenol', 0.88),

    # === 半导体（要求特定元素组合） ===
    ('semicon_GaAs', 'Semiconductor_Materials', {'Ga', 'As'}, 0, 4, None, 0.92),
    ('semicon_GaN', 'Semiconductor_Materials', {'Ga', 'N'}, 0, 4, None, 0.92),
    ('semicon_InP', 'Semiconductor_Materials', {'In', 'P'}, 0, 4, None, 0.92),
    ('semicon_InAs', 'Semiconductor_Materials', {'In', 'As'}, 0, 4, None, 0.92),
    ('semicon_GaP', 'Semiconductor_Materials', {'Ga', 'P'}, 0, 4, None, 0.92),
    ('semicon_CdTe', 'Semiconductor_Materials', {'Cd', 'Te'}, 0, 3, None, 0.92),
    ('semicon_CdSe', 'Semiconductor_Materials', {'Cd', 'Se'}, 0, 3, None, 0.90),

    # === 电池材料 ===
    ('battery_Li', 'Battery_Materials', {'Li'}, 0, 4, None, 0.88),

    # === 钙钛矿 ===
    ('perovskite', 'Perovskite_materials', {'Ti', 'O'}, 0, 5, None, 0.85),
    ('perovskite_Nb', 'Perovskite_materials', {'Nb', 'O'}, 0, 5, None, 0.85),
]


def extract_true_elements(name):
    """从名称中提取真正的化学元素"""
    elements = set()

    if not name:
        return elements

    # 清理前缀：如 (A)TiO2 -> TiO2
    cleaned = re.sub(r'^\([A-Za-z0-9]\)[\s\-]*', '', name)
    cleaned = re.sub(r'^[0-9]+[\s\-]*', '', cleaned)

    # 提取化学式token
    chem_tokens = re.findall(r'[A-Z][a-z]?\d*', cleaned)

    for token in chem_tokens:
        elem_match = re.match(r'([A-Z][a-z]?)', token)
        if elem_match:
            elem = elem_match.group(1)
            if elem in KNOWN_ELEMENTS and elem not in NON_ELEMENT_PATTERNS:
                elements.add(elem)

    # 如果元素太少（<=1）且名称长度>8，可能是普通单词而非化学式
    if len(elements) <= 1 and len(cleaned) > 8:
        # 但是单元素如 "Carbon" 也算有效
        if not (len(elements) == 1 and len(cleaned) <= 10):
            return set()

    # 如果元素超过6个，可能是复杂有机物名称，排除
    if len(elements) > 6:
        return set()

    return elements


def match_rules(elements, clean_name):
    """根据元素和名称匹配规则"""
    if not elements:
        return None

    # 1. 先尝试已知化学品精确映射
    name_clean = re.sub(r'^\([A-Za-z0-9]\)[\s\-]*', '', clean_name)
    name_clean = re.sub(r'^[0-9]+[\s\-]*', '', name_clean)

    # 提取化学式（如 TiO2、Al2O3）
    formula_match = re.search(r'[A-Z][a-z]?\d*(?:[A-Z][a-z]?\d*)+', name_clean)
    if formula_match:
        formula = formula_match.group(0)
        if formula in KNOWN_CHEMICALS:
            class_keyword, conf = KNOWN_CHEMICALS[formula]
            return (class_keyword, conf, 'known_chemical')

    # 2. 应用规则
    best_match = None
    best_conf = 0.0

    for rule_name, class_keyword, required, min_extra, max_elems, exclude_pattern, conf in RULES:
        # 检查排除模式（名称正则）
        if exclude_pattern and re.search(exclude_pattern, clean_name, re.IGNORECASE):
            continue

        # 检查必须元素
        if required and not required.issubset(elements):
            continue

        # 检查元素数量上限
        if max_elems and len(elements) > max_elems:
            continue

        # 检查最少额外元素
        extra = elements - required
        if len(extra) < min_extra:
            continue

        if conf > best_conf:
            best_conf = conf
            best_match = (class_keyword, conf, rule_name)

    return best_match


class Neo4jBelongsTo:
    def __init__(self, uri, user, password, database):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database

    def close(self):
        self.driver.close()

    def get_unmatched_chm_entities(self):
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (e:Entity:CHM)
                WHERE NOT EXISTS((e)-[:belongsTo]->())
                  AND e.cleanName IS NOT NULL
                  AND e.cleanName <> ''
                RETURN e.name as name, e.cleanName as cleanName
                ORDER BY e.name
            """)
            return [dict(r) for r in result]

    def find_classification(self, keyword):
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (c:Classification)
                WHERE toLower(c.name) CONTAINS toLower($keyword)
                RETURN c.name as name
                ORDER BY size(c.name)
                LIMIT 1
            """, keyword=keyword)
            r = result.single()
            return r['name'] if r else None

    def create_belongsTo(self, entity_name, class_name, method, confidence, elements_str):
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (e:Entity:CHM {name: $entity_name})
                MATCH (c:Classification {name: $class_name})
                MERGE (e)-[r:belongsTo]->(c)
                SET r.method = $method,
                    r.confidence = $confidence,
                    r.elements = $elements,
                    r.created_at = datetime()
                RETURN count(r) as created
            """,
            entity_name=entity_name, class_name=class_name,
            method=method, confidence=confidence, elements=elements_str)
            record = result.single()
            return record['created'] if record else 0

    def create_fusion_method_record(self, method_id, name, count):
        with self.driver.session(database=self.database) as session:
            session.run("""
                MERGE (fm:FusionMethod {method_id: $method_id})
                SET fm.relation_type = 'belongsTo',
                    fm.name = $name,
                    fm.description = '严格化学式识别+已知化学品映射+改进规则',
                    fm.confidence_threshold = 0.75,
                    fm.match_count = $count,
                    fm.quality_level = 'to_verify',
                    fm.status = 'pending_verification',
                    fm.updated_at = datetime()
            """, method_id=method_id, name=name, count=count)


def main():
    print("=" * 60)
    print("belongsTo Layer 1 V2：严格化学式规则匹配（改进版）")
    print("=" * 60)

    matcher = Neo4jBelongsTo(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE)

    try:
        entities = matcher.get_unmatched_chm_entities()
        print(f"\n[1/3] 获取到 {len(entities)} 个未匹配 CHM 实体")

        print("[2/3] 执行匹配...")
        matches = []
        no_elements = 0
        no_rule = 0
        no_class = 0

        for i, entity in enumerate(entities):
            if (i + 1) % 500 == 0:
                print(f"      进度: {i+1}/{len(entities)}, 已匹配: {len(matches)}")

            elements = extract_true_elements(entity['cleanName'])
            if not elements:
                no_elements += 1
                continue

            rule_result = match_rules(elements, entity['cleanName'])
            if not rule_result:
                no_rule += 1
                continue

            class_keyword, conf, rule_name = rule_result
            class_name = matcher.find_classification(class_keyword)
            if not class_name:
                no_class += 1
                continue

            matches.append({
                'entity': entity['name'],
                'class': class_name,
                'method': 'element_rule_v2',
                'confidence': conf,
                'elements': ','.join(sorted(elements)),
                'rule': rule_name
            })

        print(f"\n      统计:")
        print(f"      无法提取元素: {no_elements}")
        print(f"      无匹配规则: {no_rule}")
        print(f"      无对应Classification: {no_class}")
        print(f"      成功匹配: {len(matches)}")

        # 规则分布
        rule_counts = {}
        for m in matches:
            r = m['rule']
            rule_counts[r] = rule_counts.get(r, 0) + 1
        print(f"\n      规则分布:")
        for r, c in sorted(rule_counts.items(), key=lambda x: -x[1])[:10]:
            print(f"        {r}: {c}")

        print(f"\n[3/3] 写入 Neo4j...")
        created = 0
        for match in matches:
            created += matcher.create_belongsTo(
                match['entity'], match['class'],
                match['method'], match['confidence'], match['elements']
            )

        print(f"      成功创建 {created} 条 belongsTo 边")
        matcher.create_fusion_method_record('belongsTo_element_rule_v2',
                                            '严格化学式规则匹配 V2', created)
        print("\n完成！")

    finally:
        matcher.close()


if __name__ == "__main__":
    main()