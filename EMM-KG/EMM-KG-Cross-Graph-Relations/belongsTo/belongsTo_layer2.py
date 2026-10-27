import os
"""
belongsTo Layer 2 V2 - 图结构推断
通过 CHM 实体在 MatKG 中连接的邻居实体推断材料类别
"""

import re
from collections import Counter
from neo4j import GraphDatabase

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = "testcypher1.db"

# ============ 邻居实体关键词 → Classification 映射 ============
# 当 CHM 实体连接的邻居实体名称包含这些关键词时，推断其类别
NEIGHBOR_KEYWORD_RULES = [
    # === 磁性材料 ===
    (r'magnetic|magnet|ferrite|ferromagnetic|ferrimagnetic', 'Magnetic_materials', 0.85),

    # === 半导体 ===
    (r'semiconductor|band gap|bandgap', 'Semiconductor_Materials', 0.85),

    # === 超导 ===
    (r'superconduct|superconductor', 'Superconducting_materials', 0.88),

    # === 电池 ===
    (r'battery|cathode|anode|electrochemical|lithium ion|li-ion', 'Battery_Materials', 0.85),

    # === 催化 ===
    (r'catalyst|catalytic|catalysis', 'Catalyst_materials', 0.85),

    # === 光学 ===
    (r'optical|luminescence|fluorescence|phosphor|laser|photoluminescence', 'Optical_materials', 0.82),
    (r'luminescent|phosphor', 'Luminescent_materials', 0.85),
    (r'laser', 'Laser_materials', 0.85),

    # === 电子/介电 ===
    (r'dielectric|ferroelectric|piezoelectric', 'Dielectric_Materials', 0.85),
    (r'ferroelectric', 'Ferroelectric_materials', 0.88),
    (r'piezoelectric', 'Piezoelectric_materials', 0.88),

    # === 热电 ===
    (r'thermoelectric|seebeck|figure of merit', 'Thermoelectric_materials', 0.88),

    # === 生物材料 ===
    (r'biocompatible|bioactive|biomaterial|tissue|cells|in vivo|in vitro', 'Biomedical_Materials', 0.82),

    # === 涂层/薄膜 ===
    (r'coating|thin film|film|substrate', 'Inorganic_coatings_and_films', 0.80),

    # === 纳米材料 ===
    (r'nanoparticle|nanotube|nanowire|nanosheet|nanocrystal|quantum dot', 'Nanomaterials', 0.85),

    # === 复合材料 ===
    (r'composite|reinforcement|matrix|fiber reinforced', 'Composites', 0.82),

    # === 聚合物 ===
    (r'polymer|polyethylene|polystyrene|polypropylene|elastomer', 'Polymer', 0.85),

    # === 陶瓷/玻璃 ===
    (r'ceramic|glass|sinter|refractory', 'Advanced_Ceramics', 0.82),

    # === 金属/合金 ===
    (r'steel|alloy|corrosion|tensile|hardness|ductility', 'Alloys', 0.80),

    # === 能源材料 ===
    (r'solar cell|photovoltaic|fuel cell|supercapacitor', 'Energy_Materials', 0.85),

    # === 核材料 ===
    (r'nuclear|radioactive|radiation', 'Nuclear_energy_materials', 0.85),

    # === 多孔材料 ===
    (r'porous|mesoporous|microporous|adsorption|bet|surface area', 'Porous_materials', 0.82),

    # === 形状记忆 ===
    (r'shape memory|martensite|austenite', 'Shape_memory_alloys', 0.88),

    # === 储氢 ===
    (r'hydrogen storage|hydride|hydrogenation', 'Hydrogen_storage_materials', 0.85),

    # === 热电 ===
    (r'thermoelectric|seebeck', 'Thermoelectric_materials', 0.88),
]

# ============ 化学式 → 直接分类映射 ============
CHEMICAL_CLASS_MAP = {
    # 钙钛矿结构
    'BaTiO3': 'Perovskite_materials',
    'SrTiO3': 'Perovskite_materials',
    'CaTiO3': 'Perovskite_materials',
    'PbTiO3': 'Perovskite_materials',
    'BaZrO3': 'Perovskite_materials',
    'KNbO3': 'Perovskite_materials',
    'NaNbO3': 'Perovskite_materials',

    # 尖晶石
    'MgAl2O4': 'Spinel',
    'ZnAl2O4': 'Spinel',
    'Fe3O4': 'Spinel',
    'CoFe2O4': 'Spinel',
    'NiFe2O4': 'Spinel',
    'MnFe2O4': 'Spinel',

    # 石榴石
    'Y3Al5O12': 'Garnet',
    'Y3Fe5O12': 'Garnet',

    # 萤石结构
    'CeO2': 'Fluorite',
    'ZrO2': 'Fluorite',
    'UO2': 'Nuclear_energy_materials',

    # MAX相
    'Ti3SiC2': 'MAX_phase',
    'Ti3AlC2': 'MAX_phase',
    'Ti2AlC': 'MAX_phase',

    # MXene
    'Ti3C2': 'MXene',
    'Ti2C': 'MXene',

    # 沸石
    'ZSM-5': 'Zeolite',
    'Beta': 'Zeolite',
    'MCM-41': 'Zeolite',
    'SBA-15': 'Zeolite',
}


class Neo4jBelongsTo:
    def __init__(self, uri, user, password, database):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database

    def close(self):
        self.driver.close()

    def get_chm_with_neighbors(self):
        """获取未匹配的 CHM 实体及其邻居实体"""
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (e:Entity:CHM)
                WHERE NOT EXISTS((e)-[:belongsTo]->())
                  AND NOT EXISTS((e)-[:isSameAs]->())
                OPTIONAL MATCH (e)-[:RELATION]->(neighbor:Entity)
                WHERE neighbor.cleanName IS NOT NULL
                RETURN e.name as name, e.cleanName as cleanName,
                       collect(DISTINCT neighbor.cleanName) as neighbors
                ORDER BY e.name
            """)
            return [dict(r) for r in result]

    def find_classification_by_name(self, name):
        """通过名称精确或模糊匹配 Classification"""
        with self.driver.session(database=self.database) as session:
            # 先精确匹配
            result = session.run("""
                MATCH (c:Classification)
                WHERE toLower(c.name) = toLower($name)
                RETURN c.name as name
                LIMIT 1
            """, name=name)
            r = result.single()
            if r:
                return r['name']

            # 模糊匹配
            result = session.run("""
                MATCH (c:Classification)
                WHERE toLower(c.name) CONTAINS toLower($name)
                RETURN c.name as name
                ORDER BY size(c.name)
                LIMIT 1
            """, name=name)
            r = result.single()
            return r['name'] if r else None

    def create_belongsTo(self, entity_name, class_name, method, confidence, evidence):
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (e:Entity:CHM {name: $entity_name})
                MATCH (c:Classification {name: $class_name})
                MERGE (e)-[r:belongsTo]->(c)
                SET r.method = $method,
                    r.confidence = $confidence,
                    r.evidence = $evidence,
                    r.created_at = datetime()
                RETURN count(r) as created
            """,
                                 entity_name=entity_name, class_name=class_name,
                                 method=method, confidence=confidence, evidence=evidence)
            record = result.single()
            return record['created'] if record else 0

    def create_fusion_method_record(self, method_id, name, count):
        with self.driver.session(database=self.database) as session:
            session.run("""
                MERGE (fm:FusionMethod {method_id: $method_id})
                SET fm.relation_type = 'belongsTo',
                    fm.name = $name,
                    fm.description = '通过MatKG图结构邻居推断材料类别',
                    fm.confidence_threshold = 0.80,
                    fm.match_count = $count,
                    fm.quality_level = 'to_verify',
                    fm.status = 'pending_verification',
                    fm.updated_at = datetime()
            """, method_id=method_id, name=name, count=count)


def extract_clean_formula(name):
    """提取干净的化学式"""
    cleaned = re.sub(r'^\([A-Za-z0-9]\)[\s\-]*', '', name)
    cleaned = re.sub(r'^[0-9]+[\s\-]*', '', cleaned)
    return cleaned


def match_by_neighbors(neighbors):
    """通过邻居实体关键词推断类别"""
    if not neighbors:
        return None

    all_text = ' '.join(neighbors).lower()
    best_match = None
    best_conf = 0.0

    for pattern, class_name, conf in NEIGHBOR_KEYWORD_RULES:
        if re.search(pattern, all_text, re.IGNORECASE):
            if conf > best_conf:
                best_conf = conf
                best_match = (class_name, conf)

    return best_match


def match_by_chemical_formula(name):
    """通过化学式精确映射"""
    cleaned = extract_clean_formula(name)
    formula_match = re.search(r'[A-Z][a-z]?\d*(?:[A-Z][a-z]?\d*)+', cleaned)
    if formula_match:
        formula = formula_match.group(0)
        if formula in CHEMICAL_CLASS_MAP:
            return CHEMICAL_CLASS_MAP[formula], 0.92
    return None, 0


def main():
    print("=" * 60)
    print("belongsTo Layer 2 V2：图结构推断")
    print("=" * 60)

    matcher = Neo4jBelongsTo(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE)

    try:
        entities = matcher.get_chm_with_neighbors()
        print(f"\n[1/3] 获取到 {len(entities)} 个未匹配 CHM 实体（含邻居信息）")

        print("[2/3] 执行图结构推断...")
        matches = []
        no_neighbors = 0
        no_match = 0
        no_class = 0

        for i, entity in enumerate(entities):
            if (i + 1) % 500 == 0:
                print(f"      进度: {i + 1}/{len(entities)}, 已匹配: {len(matches)}")

            class_name = None
            conf = 0
            method = ''
            evidence = ''

            # 策略1：化学式精确映射
            chem_class, chem_conf = match_by_chemical_formula(entity['cleanName'])
            if chem_class:
                class_name = chem_class
                conf = chem_conf
                method = 'chem_formula_map'
                evidence = entity['cleanName']

            # 策略2：邻居图结构推断
            if not class_name and entity['neighbors']:
                neighbor_match = match_by_neighbors(entity['neighbors'])
                if neighbor_match:
                    class_name, conf = neighbor_match
                    method = 'graph_inference'
                    # 找到匹配的邻居
                    all_text = ' '.join(entity['neighbors']).lower()
                    matched_neighbors = [n for n in entity['neighbors']
                                         if n.lower() in all_text][:3]
                    evidence = ' | '.join(matched_neighbors)

            if not class_name:
                if not entity['neighbors']:
                    no_neighbors += 1
                else:
                    no_match += 1
                continue

            # 查找 Classification
            class_node = matcher.find_classification_by_name(class_name)
            if not class_node:
                no_class += 1
                continue

            matches.append({
                'entity': entity['name'],
                'class': class_node,
                'method': method,
                'confidence': conf,
                'evidence': evidence
            })

        print(f"\n      统计:")
        print(f"      无邻居实体: {no_neighbors}")
        print(f"      邻居无匹配: {no_match}")
        print(f"      无对应Classification: {no_class}")
        print(f"      成功匹配: {len(matches)}")

        # 方法分布
        method_counts = Counter(m['method'] for m in matches)
        print(f"\n      方法分布:")
        for m, c in method_counts.most_common():
            print(f"        {m}: {c}")

        print(f"\n[3/3] 写入 Neo4j...")
        created = 0
        for match in matches:
            created += matcher.create_belongsTo(
                match['entity'], match['class'],
                match['method'], match['confidence'], match['evidence']
            )

        print(f"      成功创建 {created} 条 belongsTo 边")
        matcher.create_fusion_method_record('belongsTo_graph_inference',
                                            '图结构推断', created)
        print("\n完成！")

    finally:
        matcher.close()


if __name__ == "__main__":
    main()