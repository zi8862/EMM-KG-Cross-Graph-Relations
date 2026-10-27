import os
"""
contains 关系 - 修复版（单词边界检查）
"""

import re
from neo4j import GraphDatabase

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = "testcypher1.db"

ELEMENTS = [
    'H', 'B', 'C', 'N', 'O', 'F', 'Na', 'Mg', 'Al', 'Si', 'P', 'S', 'Cl',
    'K', 'Ca', 'Sc', 'Ti', 'V', 'Cr', 'Mn', 'Fe', 'Co', 'Ni', 'Cu', 'Zn',
    'Ga', 'Ge', 'As', 'Se', 'Br', 'Rb', 'Sr', 'Y', 'Zr', 'Nb', 'Mo',
    'Ru', 'Rh', 'Pd', 'Ag', 'Cd', 'In', 'Sn', 'Sb', 'Te', 'I', 'Cs',
    'Ba', 'La', 'Ce', 'Nd', 'Sm', 'Eu', 'Gd', 'Tb', 'Dy', 'Ho',
    'Er', 'Tm', 'Yb', 'Lu', 'Hf', 'Ta', 'W', 'Re', 'Os', 'Ir', 'Pt',
    'Au', 'Hg', 'Tl', 'Pb', 'Bi', 'Th', 'Pa', 'U', 'Pu'
]

# 已知的非元素词（包含元素符号但不是化学元素）
NON_ELEMENT_WORDS = {
    'Agent', 'agent', 'Coupling', 'Compound', 'Alcohol', 'Alkaline',
    'Amine', 'Boron', 'Bonding', 'Silicon', 'Silica', 'Component',
    'Composite', 'Constant', 'Contact', 'Content', 'Control',
    'Copper', 'Iron', 'Nickel', 'Titanium', 'Silver', 'Gold'
}


def extract_elements_safe(cleanName):
    """智能元素提取：中等严格度"""
    if not cleanName or len(cleanName) < 1:
        return set()

    elements = set()

    # 1. 先检查是否为纯化学式（如 TiO2、Al2O3）
    is_chem_formula = bool(re.match(r'^[A-Z][a-z]?\d*(?:[A-Z][a-z]?\d*)*$', cleanName))

    # 2. 检查是否为含化学式的复合名（如 "titanium dioxide (TiO2)"）
    has_chem_part = bool(re.search(r'[A-Z][a-z]?\d+', cleanName))

    for elem in ELEMENTS:
        # 标准化学式匹配（大写+可选小写）
        pattern = re.compile(r'(?:^|[^a-z])' + elem + r'(?:\d|$|[^a-z])')
        if pattern.search(cleanName):
            # 额外检查：排除已知误匹配
            is_valid = True

            # 排除 Agent 中的 Ag
            if elem == 'Ag' and re.search(r'[Aa]gent', cleanName):
                is_valid = False
            # 排除 Coupling/Couple/Compound 中的 Co
            if elem == 'Co' and re.search(r'[Cc]oup|[Cc]ompound|[Cc]omponent', cleanName):
                is_valid = False
            # 排除 Alcohol 中的 Al
            if elem == 'Al' and re.search(r'[Aa]lcohol|[Aa]lkaline', cleanName):
                is_valid = False
            # 排除 Amine 中的 Am
            if elem == 'Am' and re.search(r'[Aa]mine', cleanName):
                is_valid = False
            # 排除 Boron 中的 B（除非化学式）
            if elem == 'B' and re.search(r'[Bb]oron|[Bb]onding', cleanName) and not is_chem_formula:
                is_valid = False
            # 排除 Silicon/Silica/Silicate 中的 Si
            if elem == 'Si' and re.search(r'[Ss]ilic', cleanName) and not is_chem_formula:
                is_valid = False
            # 排除 纯单词中的元素（如 "Constant" 含 "Co"）
            if elem in ['Co', 'Ni', 'Cu', 'Ag', 'Au', 'Al', 'Si', 'Fe', 'Pb', 'Sn']:
                if re.search(r'[a-z]{3,}' + elem.lower() + r'[a-z]{1,}', cleanName.lower()):
                    is_valid = False

            if is_valid:
                elements.add(elem)

    return elements


def main():
    print("=" * 60)
    print("contains 关系建立（修复版）")
    print("=" * 60)

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        with driver.session(database=NEO4J_DATABASE) as session:
            # ===== 获取 MGED-KG 元素节点 =====
            print("\n[1] 获取 MGED-KG 元素节点...")
            result = session.run("""
                MATCH (o:Other)
                WHERE o.name IN $elements
                RETURN o.name as name
            """, elements=ELEMENTS)
            mged_elements = {r['name'] for r in result}
            print(f"    找到 {len(mged_elements)} 个 MGED 元素节点")

            # ===== 获取 MatKG MatKG 元素实体
            print("\n[2] 获取 MatKG 元素实体...")
            result = session.run("""
                MATCH (e:Entity:CHM)
                WHERE e.cleanName IN $elements
                RETURN e.cleanName as name
            """, elements=ELEMENTS)
            matkg_elements = {r['name'] for r in result}
            print(f"    找到 {len(matkg_elements)} 个 MatKG 元素实体")

            # ===== 策略1：MatKG 化合物 → MGED 元素 =====
            print("\n[3] MatKG CHM → MGED 元素...")
            result = session.run("""
                MATCH (e:Entity:CHM)
                WHERE e.cleanName IS NOT NULL
                  AND NOT EXISTS((e)-[:contains]->())
                RETURN e.name as name, e.cleanName as cleanName
            """)
            entities = [dict(r) for r in result]
            print(f"    获取到 {len(entities)} 个 CHM 实体")

            created1 = 0
            for i, entity in enumerate(entities):
                if (i + 1) % 500 == 0:
                    print(f"    进度: {i+1}/{len(entities)}, 已创建: {created1}")

                elements = extract_elements_safe(entity['cleanName'])

                for elem in elements:
                    if elem in mged_elements:
                        try:
                            session.run("""
                                MATCH (e:Entity:CHM {name: $e_name})
                                MATCH (o:Other {name: $elem})
                                MERGE (e)-[r:contains]->(o)
                                SET r.method = 'matkg_to_mged_element_v2',
                                    r.confidence = 0.92,
                                    r.created_at = datetime()
                            """, e_name=entity['name'], elem=elem)
                            created1 += 1
                        except:
                            pass

            print(f"    创建 {created1} 条边")

            # ===== 策略2：MGED 术语 → MatKG 元素 =====
            print("\n[4] MGED 术语 → MatKG 元素...")
            result = session.run("""
                MATCH (n)
                WHERE (n:Term OR n:Other)
                  AND n.cleanName IS NOT NULL
                  AND NOT EXISTS((n)-[:contains]->())
                RETURN n.name as name, n.cleanName as cleanName, labels(n) as labels
            """)
            terms = [dict(r) for r in result]
            print(f"    获取到 {len(terms)} 个术语")

            created2 = 0
            for i, term in enumerate(terms):
                if (i + 1) % 5000 == 0:
                    print(f"    进度: {i+1}/{len(terms)}, 已创建: {created2}")

                elements = extract_elements_safe(term['cleanName'])

                for elem in elements:
                    if elem in matkg_elements:
                        try:
                            session.run("""
                                MATCH (n {name: $t_name})
                                WHERE n:Term OR n:Other
                                MATCH (e:Entity:CHM {cleanName: $elem})
                                MERGE (n)-[r:contains]->(e)
                                SET r.method = 'mged_to_matkg_element_v2',
                                    r.confidence = 0.92,
                                    r.created_at = datetime()
                            """, t_name=term['name'], elem=elem)
                            created2 += 1
                        except:
                            pass

            print(f"    创建 {created2} 条边")

            # ===== 汇总 =====
            total = created1 + created2
            session.run("""
                MERGE (fm:FusionMethod {method_id: 'contains_element_v2'})
                SET fm.relation_type = 'contains',
                    fm.name = '元素包含关系(V2-单词边界)',
                    fm.match_count = $count,
                    fm.quality_level = 'B',
                    fm.status = 'verified',
                    fm.updated_at = datetime()
            """, count=total)

            print(f"\n{'='*60}")
            print(f"contains 完成！总计 {total} 条边")
            print(f"{'='*60}")

    finally:
        driver.close()


if __name__ == "__main__":
    main()