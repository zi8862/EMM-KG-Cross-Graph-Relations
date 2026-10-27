import os
"""
belongsTo Layer 3 - 加权图结构推断
利用 RELATION 的 count 属性作为邻居权重
高频共现的邻居更有分类说服力
"""

import re
from collections import defaultdict
from neo4j import GraphDatabase

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = "testcypher1.db"

# 扩展的邻居关键词规则（删除过于泛化的词）
NEIGHBOR_RULES_V2 = [
    # 格式: (正则, Classification, 最小共现频率阈值, 置信度)

    # 超导（需要强证据）
    (r'superconduct|superconductor', 'Low_temperature_superconducting_materials', 1, 0.92),
    (r'critical current|critical temperature|meissner', 'Low_temperature_superconducting_materials', 1, 0.90),

    # 磁性
    (r'ferromagnetic|ferrimagnetic|antiferromagnetic', 'Magnetic_materials', 1, 0.90),
    (r'magnetic moment|coercivity|magnetization|curie temperature', 'Magnetic_materials', 3, 0.85),

    # 铁电/压电
    (r'ferroelectric|piezoelectric', 'Ferroelectric_materials', 1, 0.90),
    (r'piezoelectric coefficient|polarization', 'Piezoelectric_materials', 2, 0.85),

    # 热电
    (r'thermoelectric|seebeck coefficient|zt|figure of merit', 'Thermoelectric_materials', 1, 0.90),

    # 催化
    (r'catalyst|catalytic|catalysis|photocatalytic', 'Catalyst_materials', 2, 0.85),

    # 电池/储能
    (r'cathode material|anode material|li-ion|lithium ion|electrochemical performance', 'Battery_Materials', 2, 0.88),
    (r'specific capacity|cycle performance|coulombic efficiency', 'Battery_Materials', 3, 0.85),

    # 发光/光学
    (r'photoluminescence|electroluminescence|cathodoluminescence', 'Luminescent_materials', 1, 0.88),
    (r'phosphor|emission spectrum|quantum yield|color coordinate', 'Luminescent_materials', 2, 0.85),
    (r'laser', 'Laser_materials', 1, 0.85),

    # 半导体
    (
    r'semiconductor|band structure|band gap|conduction band|valence band', 'Compound_semiconductor_materials', 2, 0.88),

    # 生物医用
    (r'biocompatible|bioactive|cell viability|cytotoxicity|antibacterial', 'Basics_of_Biomedical_Materials', 2, 0.85),
    (r'drug delivery|tissue engineering|bone regeneration|wound healing', 'Basics_of_Biomedical_Materials', 1, 0.88),

    # 纳米材料
    (r'nanoparticle|nanotube|nanowire|nanorod|nanosheet|quantum dot', 'Functional_nanomaterials', 2, 0.88),

    # 复合材料
    (r'reinforcement|matrix composite|fiber reinforced', 'Composites', 3, 0.82),

    # 形状记忆
    (r'shape memory|martensitic transformation|austenite|martensite', 'Shape_memory_alloys', 1, 0.88),

    # 储氢/能源
    (r'hydrogen storage|hydrogen absorption|metal hydride', 'Hydrogen_storage_materials', 1, 0.88),
    (r'solar cell|photovoltaic|perovskite solar', 'Energy_Materials', 2, 0.85),

    # 涂层/薄膜
    (r'thin film|coating|sputtering|pld|cvd|atomic layer deposition', 'Inorganic_coatings_and_films', 3, 0.82),

    # 核材料
    (r'nuclear fuel|nuclear reactor|radiation damage|fission', 'Nuclear_energy_materials', 2, 0.88),

    # 光学介电
    (r'dielectric constant|permittivity|refractive index|birefringence', 'Optical_dielectric_materials', 3, 0.82),

    # 多孔材料
    (r'mesoporous|microporous|zeolite|mof|metal-organic framework', 'Porous_materials', 1, 0.88),
    (r'surface area|pore size|adsorption|bet', 'Porous_materials', 4, 0.80),

    # 聚合物
    (r'polymerization|monomer|copolymer|crosslinking', 'Polymer_Science', 2, 0.85),
    (r'glass transition|molecular weight|polydispersity', 'Polymer_Science', 3, 0.82),

    # 陶瓷
    (r'sintering|ceramic|refractory|thermal barrier', 'Advanced_Ceramics', 2, 0.85),

    # 腐蚀/防护
    (r'corrosion resistance|corrosion rate|oxidation resistance', 'Corrosion_Resistant_Alloys', 2, 0.85),

    # 碳材料
    (r'graphene|carbon nanotube|carbon fiber|activated carbon', 'Carbon_Materials', 1, 0.88),
]


class Neo4jBelongsTo:
    def __init__(self, uri, user, password, database):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database

    def close(self):
        self.driver.close()

    def get_chm_with_weighted_neighbors(self):
        """获取未匹配的 CHM 实体及其加权邻居"""
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (e:Entity:CHM)
                WHERE NOT EXISTS((e)-[:belongsTo]->())
                  AND NOT EXISTS((e)-[:isSameAs]->())
                OPTIONAL MATCH (e)-[rel:RELATION]->(neighbor:Entity)
                WHERE neighbor.cleanName IS NOT NULL
                RETURN e.name as name, e.cleanName as cleanName,
                       collect({name: neighbor.cleanName, count: coalesce(rel.count, 1)}) as neighbors
                ORDER BY e.name
            """)
            return [dict(r) for r in result]

    def find_classification_by_name(self, name):
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (c:Classification)
                WHERE toLower(c.name) = toLower($name)
                   OR toLower(c.name) CONTAINS toLower($name)
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
                    fm.match_count = $count,
                    fm.quality_level = 'to_verify',
                    fm.status = 'pending_verification',
                    fm.updated_at = datetime()
            """, method_id=method_id, name=name, count=count)


def match_weighted(neighbors):
    """加权匹配：考虑共现频率"""
    if not neighbors:
        return None

    # 聚合邻居文本（按频次加权）
    aggregated = defaultdict(float)
    for n in neighbors:
        name = n['name'].lower() if n['name'] else ''
        count = n['count'] if n['count'] else 1
        aggregated[name] += count

    # 按频次排序的邻居列表
    sorted_neighbors = sorted(aggregated.items(), key=lambda x: -x[1])

    best_match = None
    best_conf = 0.0
    best_evidence = ''

    for pattern, class_name, min_freq, conf in NEIGHBOR_RULES_V2:
        total_weight = 0
        matched_neighbors = []

        for neighbor_name, freq in sorted_neighbors:
            if re.search(pattern, neighbor_name, re.IGNORECASE):
                total_weight += freq
                matched_neighbors.append(f"{neighbor_name}({freq})")
                if total_weight >= min_freq:
                    # 加权调整置信度
                    weight_bonus = min(0.05, total_weight / 100)
                    adjusted_conf = conf + weight_bonus

                    if adjusted_conf > best_conf:
                        best_conf = adjusted_conf
                        best_match = class_name
                        best_evidence = ' | '.join(matched_neighbors[:3])

    if best_match:
        return best_match, min(best_conf, 0.95), best_evidence
    return None


def main():
    print("=" * 60)
    print("belongsTo Layer 3：加权图结构推断")
    print("=" * 60)

    matcher = Neo4jBelongsTo(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE)

    try:
        entities = matcher.get_chm_with_weighted_neighbors()
        print(f"\n[1/3] 获取到 {len(entities)} 个未匹配 CHM 实体")

        print("[2/3] 加权匹配...")
        matches = []
        no_match = 0
        no_class = 0

        for i, entity in enumerate(entities):
            if (i + 1) % 500 == 0:
                print(f"      进度: {i + 1}/{len(entities)}, 已匹配: {len(matches)}")

            result = match_weighted(entity['neighbors'])
            if not result:
                no_match += 1
                continue

            class_name, conf, evidence = result
            class_node = matcher.find_classification_by_name(class_name)
            if not class_node:
                no_class += 1
                continue

            matches.append({
                'entity': entity['name'],
                'class': class_node,
                'confidence': conf,
                'evidence': evidence
            })

        print(f"\n      统计:")
        print(f"      无匹配: {no_match}")
        print(f"      无对应Classification: {no_class}")
        print(f"      成功匹配: {len(matches)}")

        if matches:
            confs = [m['confidence'] for m in matches]
            print(f"      最高分: {max(confs):.3f}")
            print(f"      平均分: {sum(confs) / len(confs):.3f}")
            for t in [0.90, 0.85, 0.82]:
                c = sum(1 for x in confs if x >= t)
                print(f"      ≥{t}: {c}")

        print(f"\n[3/3] 写入 Neo4j...")
        created = 0
        for match in matches:
            created += matcher.create_belongsTo(
                match['entity'], match['class'],
                'weighted_graph_inference', match['confidence'], match['evidence']
            )

        print(f"      成功创建 {created} 条 belongsTo 边")
        matcher.create_fusion_method_record('belongsTo_weighted_graph',
                                            '加权图推断', created)
        print("\n完成！")

    finally:
        matcher.close()


if __name__ == "__main__":
    main()