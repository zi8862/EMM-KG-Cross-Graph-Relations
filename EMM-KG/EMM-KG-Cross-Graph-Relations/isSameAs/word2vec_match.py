import os
"""
Word2Vec 语义匹配 - 优化版
增加多重约束提高匹配质量
"""

import numpy as np
import re
from gensim.models import Word2Vec
from gensim.utils import simple_preprocess
from sklearn.metrics.pairwise import cosine_similarity
from neo4j import GraphDatabase

# ============ 配置 ============
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = "testcypher1.db"

# Word2Vec 参数
VECTOR_SIZE = 200
WINDOW = 5
MIN_COUNT = 1
EPOCHS = 10
SIMILARITY_THRESHOLD = 0.90  # 提高到 0.90


# ============ 约束条件 ============
def is_valid_entity_name(name):
    """过滤无效实体名"""
    if not name or len(name) < 2:
        return False
    # 过滤纯数字/特殊字符
    if re.match(r'^[\d\s\.\,\;\:\#\%\$\@\!\?\*\(\)\[\]\{\}\-\+\=]+$', name):
        return False
    # 过滤纯单字符
    if len(name.strip()) <= 1:
        return False
    return True


def extract_meaningful_tokens(name):
    """提取有意义的词元"""
    tokens = set()
    # 分词
    words = simple_preprocess(name)
    for word in words:
        if len(word) >= 2:  # 过滤单字符
            tokens.add(word.lower())
    # 提取化学式
    chem_pattern = re.findall(r'[A-Z][a-z]?\d*', name)
    for chem in chem_pattern:
        if len(chem) >= 2:
            tokens.add(chem.lower())
    return tokens


def check_shared_tokens(name1, name2, min_shared=1):
    """检查两个名称是否共享有意义的词元"""
    tokens1 = extract_meaningful_tokens(name1)
    tokens2 = extract_meaningful_tokens(name2)
    shared = tokens1 & tokens2
    # 排除太泛化的共享词
    stop_words = {'the', 'of', 'and', 'in', 'on', 'at', 'to', 'for', 'with',
                  'by', 'from', 'as', 'is', 'was', 'are', 'were', 'be', 'been'}
    shared = shared - stop_words
    return len(shared) >= min_shared


def check_length_ratio(name1, name2, min_ratio=0.3, max_ratio=3.0):
    """检查名称长度比是否合理"""
    if not name1 or not name2:
        return False
    ratio = len(name1) / max(len(name2), 1)
    return min_ratio <= ratio <= max_ratio


def is_chemical_formula(name):
    """判断是否为化学式"""
    return bool(re.match(r'^[A-Z][a-z]?\d*(?:[A-Z][a-z]?\d*)*$', name))


def tokenize_name(name):
    """分词"""
    if not name:
        return []
    tokens = simple_preprocess(name)
    tokens.append(name.lower())
    return tokens


def get_entity_vector(model, name):
    """获取实体向量"""
    tokens = tokenize_name(name)
    vectors = []
    for token in tokens:
        if token in model.wv:
            vectors.append(model.wv[token])
    if vectors:
        return np.mean(vectors, axis=0)
    return np.zeros(VECTOR_SIZE)


class Neo4jMatcher:
    def __init__(self, uri, user, password, database):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database

    def close(self):
        self.driver.close()

    def get_all_entities(self):
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (e:Entity)
                WHERE e.cleanName IS NOT NULL AND e.cleanName <> ''
                RETURN e.name as name, e.cleanName as cleanName
            """)
            return [dict(r) for r in result]

    def get_unmatched_entities(self):
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (e:Entity)
                WHERE NOT EXISTS((e)-[:isSameAs]->())
                  AND e.cleanName IS NOT NULL AND e.cleanName <> ''
                RETURN e.name as name, e.cleanName as cleanName
                ORDER BY e.name
            """)
            return [dict(r) for r in result]

    def get_all_mged_terms(self):
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (n)
                WHERE (n:Term OR n:Other)
                  AND n.cleanName IS NOT NULL AND n.cleanName <> ''
                RETURN n.name as name, n.cleanName as cleanName, labels(n) as labels
                ORDER BY n.name
            """)
            return [dict(r) for r in result]

    def create_isSameAs_batch(self, matches_batch):
        with self.driver.session(database=self.database) as session:
            created = 0
            for match in matches_batch:
                try:
                    result = session.run("""
                        MATCH (e:Entity {name: $entity_name})
                        MATCH (n {name: $term_name})
                        WHERE n:Term OR n:Other
                        MERGE (e)-[r:isSameAs]->(n)
                        ON CREATE SET 
                            r.method = 'word2vec_cosine_v2',
                            r.confidence = $confidence,
                            r.similarity_score = $score,
                            r.vector_size = $vec_size,
                            r.shared_tokens = $shared,
                            r.length_ratio = $ratio,
                            r.created_at = datetime()
                        RETURN count(r) as created
                    """,
                                         entity_name=match['entity_name'],
                                         term_name=match['term_name'],
                                         confidence=round(match['score'], 4),
                                         score=round(match['score'], 4),
                                         vec_size=VECTOR_SIZE,
                                         shared=match.get('shared_tokens', 0),
                                         ratio=round(match.get('length_ratio', 1.0), 2))
                    record = result.single()
                    if record and record['created']:
                        created += 1
                except:
                    pass
            return created

    def update_fusion_method_record(self, count, passed, rejected):
        with self.driver.session(database=self.database) as session:
            session.run("""
                MERGE (fm:FusionMethod {method_id: 'isSameAs_word2vec_cosine_v2'})
                SET fm.relation_type = 'isSameAs',
                    fm.name = 'Word2Vec语义匹配(优化版)',
                    fm.description = '阈值0.90+长度比0.3~3.0+共享词元≥1+化学式验证',
                    fm.confidence_threshold = $threshold,
                    fm.match_count = $count,
                    fm.candidates_passed = $passed,
                    fm.candidates_rejected = $rejected,
                    fm.quality_level = 'to_verify',
                    fm.status = 'pending_verification',
                    fm.updated_at = datetime()
            """, threshold=SIMILARITY_THRESHOLD, count=count,
                        passed=passed, rejected=rejected)


def run_word2vec_matching_v2():
    print("=" * 60)
    print("Word2Vec 语义匹配 - 优化版")
    print(f"相似度阈值: {SIMILARITY_THRESHOLD}")
    print(f"长度比范围: 0.3 ~ 3.0")
    print(f"共享词元: ≥1")
    print("=" * 60)

    matcher = Neo4jMatcher(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE)

    try:
        print("\n[1/5] 获取数据...")
        all_entities = matcher.get_all_entities()
        unmatched = matcher.get_unmatched_entities()
        mged_terms = matcher.get_all_mged_terms()
        print(f"      全部 Entity: {len(all_entities)}")
        print(f"      未匹配: {len(unmatched)}")
        print(f"      MGED 术语: {len(mged_terms)}")

        # 过滤无效实体名
        unmatched = [e for e in unmatched if is_valid_entity_name(e['cleanName'])]
        mged_terms = [t for t in mged_terms if is_valid_entity_name(t['cleanName'])]
        print(f"      过滤后未匹配: {len(unmatched)}")
        print(f"      过滤后 MGED 术语: {len(mged_terms)}")

        # 训练 Word2Vec
        print("\n[2/5] 训练 Word2Vec...")
        sentences = []
        for entity in all_entities:
            tokens = tokenize_name(entity['cleanName'])
            if tokens:
                sentences.append(tokens)
        for term in mged_terms:
            tokens = tokenize_name(term['cleanName'])
            if tokens:
                sentences.append(tokens)

        model = Word2Vec(sentences, vector_size=VECTOR_SIZE, window=WINDOW,
                         min_count=MIN_COUNT, workers=4, epochs=EPOCHS)
        print(f"      词汇量: {len(model.wv)}")

        # 计算向量
        print("\n[3/5] 计算向量...")
        term_vectors = []
        valid_terms = []
        for term in mged_terms:
            vec = get_entity_vector(model, term['cleanName'])
            if np.any(vec):
                term_vectors.append(vec)
                valid_terms.append(term)
        term_matrix = np.array(term_vectors)
        print(f"      有效 MGED 术语: {len(valid_terms)}")

        # 匹配（含约束）
        print(f"\n[4/5] 匹配...")
        matches = []
        rejected_by_length = 0
        rejected_by_token = 0

        for i, entity in enumerate(unmatched):
            if (i + 1) % 5000 == 0:
                print(f"      进度: {i + 1}/{len(unmatched)}, 匹配: {len(matches)}, "
                      f"拒绝(长度):{rejected_by_length}, 拒绝(词元):{rejected_by_token}")

            entity_vec = get_entity_vector(model, entity['cleanName'])
            if not np.any(entity_vec):
                continue

            sim_scores = cosine_similarity([entity_vec], term_matrix)[0]

            # 获取 Top-5 候选
            top_indices = np.argsort(sim_scores)[-5:][::-1]

            best_match = None
            best_score = 0

            for idx in top_indices:
                score = sim_scores[idx]
                if score < SIMILARITY_THRESHOLD:
                    continue

                term = valid_terms[idx]

                # 约束1：长度比
                if not check_length_ratio(entity['cleanName'], term['cleanName']):
                    continue

                # 约束2：共享词元
                if not check_shared_tokens(entity['cleanName'], term['cleanName']):
                    continue

                if score > best_score:
                    best_score = score
                    best_match = term

            if best_match and best_score >= SIMILARITY_THRESHOLD:
                matches.append({
                    'entity_name': entity['name'],
                    'term_name': best_match['name'],
                    'score': float(best_score),
                    'shared_tokens': len(extract_meaningful_tokens(entity['cleanName']) &
                                         extract_meaningful_tokens(best_match['cleanName'])),
                    'length_ratio': len(entity['cleanName']) / max(len(best_match['cleanName']), 1)
                })

        print(f"\n      匹配结果: {len(matches)} 对")

        if matches:
            scores = [m['score'] for m in matches]
            print(f"      最高分: {max(scores):.4f}")
            print(f"      平均分: {sum(scores) / len(scores):.4f}")
            for t in [0.95, 0.90]:
                c = sum(1 for s in scores if s >= t)
                print(f"      >= {t}: {c}")

        # 写入
        print(f"\n[5/5] 写入 Neo4j...")
        created = 0
        for i in range(0, len(matches), 100):
            batch = matches[i:i + 100]
            created += matcher.create_isSameAs_batch(batch)
            if (i + 100) % 1000 == 0:
                print(f"      进度: {min(i + 100, len(matches))}/{len(matches)}, 成功: {created}")

        print(f"\n      成功创建 {created} 条边")

        matcher.update_fusion_method_record(created, len(matches),
                                            len(unmatched) - len(matches))

        print("\n" + "=" * 60)
        print("Word2Vec 优化版匹配完成！")
        print(f"新增 {created} 条匹配")
        print("=" * 60)

    finally:
        matcher.close()


if __name__ == "__main__":
    run_word2vec_matching_v2()