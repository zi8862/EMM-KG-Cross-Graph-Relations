import os
"""
belongsTo Layer 4 – 基于共现统计与规则融合的分类方法
结合元素规则 + 图结构推断 + 高频共现属性关联
"""
import re
from collections import Counter
from neo4j import GraphDatabase

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = "testcypher1.db"

# 扩增后的关键词规则库（增强对有机分子、药物的覆盖）
ADVANCED_NEIGHBOR_RULES = {
    'Magnetic_materials': [r'magnetic', r'coercivity', r'curie', r'magnetization'],
    'Battery_Materials': [r'cathode', r'anode', r'specific capacity', r'li-ion'],
    'Biomedical_Materials': [r'cytotoxicity', r'antibacterial', r'cell viability', r'wound healing', r'drug'],
    'Luminescent_materials': [r'photoluminescence', r'phosphor', r'quantum yield'],
    'Thermoelectric_materials': [r'seebeck', r'ZT', r'thermoelectric'],
    'Superconducting_materials': [r'superconduct', r'meissner'],
    'Photocatalytic_materials': [r'photodegradation', r'photocatalyst'],
    'Shape_memory_alloys': [r'shape memory', r'martensite'],
    'Polymer_Materials': [r'polymer', r'elastomer', r'copolymer', r'molecular weight'],
}

def infer_by_statistical_cooccurrence(entity_name, neighbors):
    """通过高频共现属性推断：如果邻居中某种属性反复出现，则分类"""
    if not neighbors: return None, 0
    # 统计邻居中高频出现的属性词
    prop_words = Counter()
    for n in neighbors:
        name = n['name'].lower()
        count = n.get('count', 1)
        prop_words[name] += count
    # 根据属性词频次推断（例如：多次出现 'conductivity' 可能与热电/电池相关）
    if 'conductivity' in prop_words and prop_words['conductivity'] > 5:
        return 'Thermoelectric_materials', 0.78
    return None, 0

class Neo4jMatcher:
    def __init__(self, uri, user, password, database):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database

    def close(self): self.driver.close()

    def get_unmatched_chm_with_neighbors(self):
        with self.driver.session(database=self.database) as session:
            return [dict(r) for r in session.run("""
                MATCH (e:Entity:CHM) WHERE NOT EXISTS((e)-[:belongsTo]->()) AND NOT EXISTS((e)-[:isSameAs]->())
                OPTIONAL MATCH (e)-[rel:RELATION]->(neighbor:Entity)
                RETURN e.name as name, e.cleanName as cleanName,
                       collect({name: neighbor.cleanName, count: coalesce(rel.count, 1)}) as neighbors
            """)]

    def find_classification(self, keyword):
        with self.driver.session(database=self.database) as session:
            r = session.run("MATCH (c:Classification) WHERE toLower(c.name) CONTAINS toLower($k) RETURN c.name as name LIMIT 1", k=keyword).single()
            return r['name'] if r else None

    def create_and_record(self, matches):
        with self.driver.session(database=self.database) as session:
            created = 0
            for m in matches:
                session.run("""MATCH (e:Entity:CHM {name: $e}), (c:Classification {name: $c})
                              MERGE (e)-[r:belongsTo]->(c)
                              SET r.method='statistical_fusion', r.confidence=$conf, r.evidence=$ev, r.created_at=datetime()""",
                              e=m['entity'], c=m['class'], conf=m['conf'], ev=m['evidence'])
                created += 1
            return created

def main():
    matcher = Neo4jMatcher(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE)
    entities = matcher.get_unmatched_chm_with_neighbors()
    print(f"获取到 {len(entities)} 个未匹配 CHM 实体")
    matches = []
    for entity in entities:
        # 策略A：高级关键词匹配
        matched_class = None
        max_conf = 0.0
        evidence = ""
        all_text = " ".join([n['name'] for n in entity['neighbors']]).lower()
        for class_key, patterns in ADVANCED_NEIGHBOR_RULES.items():
            total_weight = sum(entity['neighbors'][i]['count'] for i, n in enumerate(entity['neighbors']) if any(re.search(p, n['name'].lower()) for p in patterns))
            if total_weight >= 2:
                conf = min(0.82 + total_weight/200, 0.92)
                if conf > max_conf: max_conf, matched_class = conf, class_key
                evidence = "High-weight keywords"
        # 策略B：统计推断
        if not matched_class:
            stat_class, stat_conf = infer_by_statistical_cooccurrence(entity['name'], entity['neighbors'])
            if stat_class: matched_class, max_conf, evidence = stat_class, stat_conf, "Statistical co-occurrence"
        if matched_class:
            class_node = matcher.find_classification(matched_class)
            if class_node:
                matches.append({'entity': entity['name'], 'class': class_node, 'conf': round(max_conf, 3), 'evidence': evidence})
    print(f"成功匹配并写入 {matcher.create_and_record(matches)} 条边")

if __name__ == "__main__":
    main()