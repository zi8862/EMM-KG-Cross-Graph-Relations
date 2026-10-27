import os
"""
contains 关系建立 - 元素包含
MatKG ↔ MGED-KG 双向元素包含关系
"""

import re
from neo4j import GraphDatabase

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = "testcypher1.db"

# 元素周期表
ELEMENTS = {
    'H', 'He', 'Li', 'Be', 'B', 'C', 'N', 'O', 'F', 'Ne',
    'Na', 'Mg', 'Al', 'Si', 'P', 'S', 'Cl', 'Ar', 'K', 'Ca',
    'Sc', 'Ti', 'V', 'Cr', 'Mn', 'Fe', 'Co', 'Ni', 'Cu', 'Zn',
    'Ga', 'Ge', 'As', 'Se', 'Br', 'Kr', 'Rb', 'Sr', 'Y', 'Zr',
    'Nb', 'Mo', 'Tc', 'Ru', 'Rh', 'Pd', 'Ag', 'Cd', 'In', 'Sn',
    'Sb', 'Te', 'I', 'Xe', 'Cs', 'Ba', 'La', 'Ce', 'Pr', 'Nd',
    'Pm', 'Sm', 'Eu', 'Gd', 'Tb', 'Dy', 'Ho', 'Er', 'Tm', 'Yb',
    'Lu', 'Hf', 'Ta', 'W', 'Re', 'Os', 'Ir', 'Pt', 'Au', 'Hg',
    'Tl', 'Pb', 'Bi', 'Po', 'At', 'Rn', 'Ra', 'Th', 'Pa', 'U',
    'Np', 'Pu', 'Am', 'Cm'
}

# 非元素缩写（在 Other 节点中出现但不是化学元素的）
NON_ELEMENTS = {'T', 'E', 'D', 'L', 'Bo', 'Hu', 'Hm', 'Mm', 'Ci', 'Az', 'Bs', 'Bt', 'Bx', 'Bz', 'Cb', 'Cn'}


class ContainsBuilder:
    def __init__(self, uri, user, password, database):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database

    def close(self):
        self.driver.close()

    def get_matkg_elements(self):
        """获取 MatKG 中可能是纯元素的 CHM 实体"""
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (e:Entity:CHM)
                WHERE e.cleanName IN $elem_list
                RETURN e.name as name, e.cleanName as cleanName
            """, elem_list=list(ELEMENTS - NON_ELEMENTS))
            return [dict(r) for r in result]

    def get_mged_elements(self):
        """获取 MGED-KG 中可能是元素的 Other 节点"""
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (o:Other)
                WHERE o.name IN $elem_list
                RETURN o.name as name
            """, elem_list=list(ELEMENTS - NON_ELEMENTS))
            return [dict(r) for r in result]

    def get_matkg_compounds(self):
        """获取 MatKG 中所有 CHM 化合物实体（非纯元素）"""
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (e:Entity:CHM)
                WHERE NOT e.cleanName IN $elem_list
                  AND e.cleanName IS NOT NULL
                RETURN e.name as name, e.cleanName as cleanName
            """, elem_list=list(ELEMENTS - NON_ELEMENTS))
            return [dict(r) for r in result]

    def get_mged_compounds(self):
        """获取 MGED-KG 中包含元素的术语"""
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (n)
                WHERE n:Term OR n:Other
                  AND n.cleanName IS NOT NULL
                  AND NOT n.name IN $elem_list
                RETURN n.name as name, n.cleanName as cleanName, labels(n) as labels
            """, elem_list=list(ELEMENTS - NON_ELEMENTS))
            return [dict(r) for r in result]

    def create_contains_edge(self, from_label, from_name, to_label, to_name,
                             method, confidence, evidence=""):
        """创建 contains 关系"""
        with self.driver.session(database=self.database) as session:
            try:
                result = session.run("""
                    MATCH (a {name: $from_name})
                    MATCH (b {name: $to_name})
                    WHERE ($from_label IS NULL OR $from_label IN labels(a))
                      AND ($to_label IS NULL OR $to_label IN labels(b))
                    MERGE (a)-[r:contains]->(b)
                    SET r.method = $method,
                        r.confidence = $confidence,
                        r.evidence = $evidence,
                        r.created_at = datetime()
                    RETURN count(r) as created
                """,
                                     from_name=from_name, to_name=to_name,
                                     from_label=from_label, to_label=to_label,
                                     method=method, confidence=confidence, evidence=evidence)
                record = result.single()
                return record['created'] if record else 0
            except:
                return 0

    def create_fusion_method_records(self, stats):
        with self.driver.session(database=self.database) as session:
            for method_id, count in stats.items():
                session.run("""
                    MERGE (fm:FusionMethod {method_id: $method_id})
                    SET fm.relation_type = 'contains',
                        fm.match_count = $count,
                        fm.quality_level = 'B',
                        fm.status = 'verified',
                        fm.updated_at = datetime()
                """, method_id=f"contains_{method_id}", count=count)


def extract_elements_from_formula(name):
    """从化学式名称中提取元素"""
    if not name:
        return set()

    # 清理：去除前缀标记
    cleaned = re.sub(r'^\([A-Za-z0-9]\)[\s\-]*', '', name)
    cleaned = re.sub(r'^[0-9]+[\s\-]*', '', cleaned)

    # 提取元素
    elements = set()
    tokens = re.findall(r'[A-Z][a-z]?', cleaned)

    for token in tokens:
        if token in ELEMENTS and token not in NON_ELEMENTS:
            elements.add(token)

    return elements


def main():
    print("=" * 60)
    print("contains 关系建立：元素包含")
    print("MatKG ↔ MGED-KG 双向")
    print("=" * 60)

    builder = ContainsBuilder(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE)

    try:
        stats = {}

        # ===== 策略1：MatKG 化合物 → MGED-KG 元素节点 =====
        print(f"\n[策略1] MatKG 化合物 → MGED-KG 元素节点")
        print("-" * 40)

        compounds = builder.get_matkg_compounds()
        mged_elements = builder.get_mged_elements()
        mged_element_names = {e['name'] for e in mged_elements}

        print(f"  MatKG 化合物: {len(compounds)}")
        print(f"  MGED-KG 元素节点: {len(mged_elements)}")

        created = 0
        for compound in compounds:
            elements = extract_elements_from_formula(compound['cleanName'])
            for elem in elements:
                if elem in mged_element_names:
                    builder.create_contains_edge(
                        'Entity', compound['name'],
                        'Other', elem,
                        'element_extraction_matkg_to_mged',
                        0.95,
                        f"化合物 {compound['cleanName']} 包含元素 {elem}"
                    )
                    created += 1

        stats['matkg_compound_to_mged_element'] = created
        print(f"  创建: {created} 条边")

        # ===== 策略2：MGED-KG 化合物 → MatKG 元素节点 =====
        print(f"\n[策略2] MGED-KG 化合物 → MatKG 元素节点")
        print("-" * 40)

        mged_compounds = builder.get_mged_compounds()
        matkg_elements = builder.get_matkg_elements()
        matkg_element_names = {e['cleanName'] for e in matkg_elements}

        print(f"  MGED-KG 化合物: {len(mged_compounds)}")
        print(f"  MatKG 元素实体: {len(matkg_elements)}")

        created = 0
        for compound in mged_compounds:
            elements = extract_elements_from_formula(compound['cleanName'])
            for elem in elements:
                if elem in matkg_element_names:
                    builder.create_contains_edge(
                        'Other', compound['name'],
                        'Entity', elem,
                        'element_extraction_mged_to_matkg',
                        0.95,
                        f"术语 {compound['cleanName']} 包含元素 {elem}"
                    )
                    created += 1

        stats['mged_compound_to_matkg_element'] = created
        print(f"  创建: {created} 条边")

        # ===== 策略3：MatKG 纯元素 → MGED-KG 元素节点（isSameAs 补充） =====
        print(f"\n[策略3] MatKG 元素 ↔ MGED-KG 元素（双向 contains）")
        print("-" * 40)

        # 如果两个图谱都有同一个元素，建立双向 contains
        common_elements = matkg_element_names & mged_element_names
        print(f"  共同元素: {len(common_elements)}")

        created = 0
        for elem in common_elements:
            # MatKG 元素 contains MGED-KG 元素
            builder.create_contains_edge(
                'Entity', elem,
                'Other', elem,
                'common_element',
                1.0,
                f"相同元素 {elem}"
            )
            # MGED-KG 元素 contains MatKG 元素
            builder.create_contains_edge(
                'Other', elem,
                'Entity', elem,
                'common_element',
                1.0,
                f"相同元素 {elem}"
            )
            created += 2

        stats['common_elements'] = created
        print(f"  创建: {created} 条边")

        # ===== 汇总 =====
        total = sum(stats.values())
        print(f"\n" + "=" * 60)
        print("contains 关系建立完成！")
        for key, value in stats.items():
            print(f"  {key}: {value}")
        print(f"  总计: {total} 条边")
        print("=" * 60)

        builder.create_fusion_method_records(stats)

    finally:
        builder.close()


if __name__ == "__main__":
    main()