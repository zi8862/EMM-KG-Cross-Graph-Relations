import os
"""
TF-IDF 语义匹配脚本（修复版）
使用 scikit-learn 的 TfidfVectorizer 进行实体对齐
"""

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from neo4j import GraphDatabase
import datetime

# ============ 配置 ============
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = "testcypher1.db"

# TF-IDF 参数
SIMILARITY_THRESHOLD = 0.78  # 余弦相似度阈值
MAX_FEATURES = 5000  # 最大特征数
NGRAM_RANGE = (2, 3)  # 使用 2-3 字符的 n-gram

# ============ Neo4j 操作 ============
class Neo4jMatcher:
    def __init__(self, uri, user, password, database):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database

    def close(self):
        self.driver.close()

    def get_unmatched_entities(self):
        """获取所有未匹配的 Entity（过滤空名称）"""
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (e:Entity)
                WHERE NOT EXISTS((e)-[:isSameAs]->())
                  AND e.cleanName IS NOT NULL
                  AND e.cleanName <> ''
                RETURN e.name as name, e.cleanName as cleanName
                ORDER BY e.name
            """)
            return [dict(record) for record in result]

    def get_all_mged_terms(self):
        """获取所有 MGED-KG 术语（过滤空名称）"""
        with self.driver.session(database=self.database) as session:
            result = session.run("""
                MATCH (n)
                WHERE n:Term OR n:Other
                  AND n.cleanName IS NOT NULL
                  AND n.cleanName <> ''
                RETURN n.name as name, n.cleanName as cleanName, 
                       labels(n) as labels
                ORDER BY n.name
            """)
            return [dict(record) for record in result]

    def create_isSameAs_batch(self, matches_batch):
        """批量创建 isSameAs 关系"""
        with self.driver.session(database=self.database) as session:
            created = 0
            errors = 0
            for match in matches_batch:
                try:
                    result = session.run("""
                        MATCH (e:Entity {name: $entity_name})
                        MATCH (n {name: $term_name})
                        WHERE n:Term OR n:Other
                        MERGE (e)-[r:isSameAs]->(n)
                        SET r.method = $method,
                            r.confidence = $confidence,
                            r.similarity_score = $score,
                            r.created_at = datetime()
                        RETURN count(r) as created
                    """,
                    entity_name=match['entity_name'],
                    term_name=match['term_name'],
                    method='tfidf_cosine',
                    confidence=round(match['score'], 4),
                    score=round(match['score'], 4))

                    record = result.single()
                    if record and record['created']:
                        created += 1
                except Exception as e:
                    errors += 1
            return created, errors

    def update_fusion_method_record(self, count, threshold):
        """更新实验记录"""
        with self.driver.session(database=self.database) as session:
            session.run("""
                MERGE (fm:FusionMethod {method_id: 'isSameAs_tfidf_cosine'})
                SET fm.relation_type = 'isSameAs',
                    fm.name = 'TF-IDF语义匹配',
                    fm.description = '使用TF-IDF向量化+余弦相似度进行实体匹配',
                    fm.confidence_threshold = $threshold,
                    fm.match_count = $count,
                    fm.quality_level = 'to_verify',
                    fm.status = 'pending_verification',
                    fm.updated_at = datetime()
            """,
            threshold=threshold,
            count=count)


# ============ 匹配逻辑 ============
def safe_text(text):
    """安全获取文本，处理 None 和空字符串"""
    if text is None or text == '':
        return 'unknown'
    return str(text)


def run_tfidf_matching():
    print("=" * 60)
    print("TF-IDF 语义匹配开始（修复版）")
    print(f"相似度阈值: {SIMILARITY_THRESHOLD}")
    print(f"N-gram 范围: {NGRAM_RANGE}")
    print(f"最大特征数: {MAX_FEATURES}")
    print("=" * 60)

    # 连接 Neo4j
    matcher = Neo4jMatcher(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE)

    try:
        # 获取数据
        print("\n[1/4] 获取未匹配的 Entity...")
        entities = matcher.get_unmatched_entities()
        print(f"      获取到 {len(entities)} 个未匹配 Entity")

        # 检查空值
        empty_count = sum(1 for e in entities if not e.get('cleanName'))
        print(f"      其中 cleanName 为空: {empty_count}")

        print("\n[2/4] 获取 MGED-KG 术语...")
        mged_terms = matcher.get_all_mged_terms()
        print(f"      获取到 {len(mged_terms)} 个 MGED 术语")

        empty_mged = sum(1 for t in mged_terms if not t.get('cleanName'))
        print(f"      其中 cleanName 为空: {empty_mged}")

        # 准备文本（过滤空值）
        entity_data = [(e, safe_text(e.get('cleanName', ''))) for e in entities]
        term_data = [(t, safe_text(t.get('cleanName', ''))) for t in mged_terms]

        entity_texts = [text for _, text in entity_data]
        term_texts = [text for _, text in term_data]

        # 样本展示
        print("\n      实体样例（前5个）:")
        for i in range(min(5, len(entity_texts))):
            print(f"        {i+1}. '{entity_texts[i]}'")

        # 合并所有文本训练 TF-IDF 向量器
        all_texts = entity_texts + term_texts
        print(f"\n[3/4] 训练 TF-IDF 向量器（共 {len(all_texts)} 个文本）...")

        vectorizer = TfidfVectorizer(
            analyzer='char',
            ngram_range=NGRAM_RANGE,
            max_features=MAX_FEATURES,
            lowercase=True,
            strip_accents='unicode'
        )

        # 拟合所有文本
        tfidf_matrix = vectorizer.fit_transform(all_texts)

        # 分离 Entity 和 Term 的向量
        entity_vectors = tfidf_matrix[:len(entity_texts)]
        term_vectors = tfidf_matrix[len(entity_texts):]

        print(f"      特征维度: {entity_vectors.shape[1]}")

        # 计算相似度
        print(f"\n[4/4] 计算相似度并匹配...")
        print(f"      （这可能需要几分钟，请耐心等待...）")

        all_matches = []
        batch_size = 2000

        for i in range(0, entity_vectors.shape[0], batch_size):
            end = min(i + batch_size, entity_vectors.shape[0])
            entity_batch = entity_vectors[i:end]

            # 计算余弦相似度
            sim_matrix = cosine_similarity(entity_batch, term_vectors)

            # 获取每行的最佳匹配
            for j in range(sim_matrix.shape[0]):
                best_idx = np.argmax(sim_matrix[j])
                best_score = sim_matrix[j][best_idx]

                if best_score >= SIMILARITY_THRESHOLD:
                    all_matches.append({
                        'entity_name': entities[i + j]['name'],
                        'term_name': mged_terms[best_idx]['name'],
                        'score': float(best_score)
                    })

            if end % 5000 == 0 or end == entity_vectors.shape[0]:
                print(f"      处理进度: {end}/{entity_vectors.shape[0]}, 已匹配: {len(all_matches)}")

        print(f"\n      匹配结果: {len(all_matches)} 对（阈值 >= {SIMILARITY_THRESHOLD}）")

        # 去重
        entity_best = {}
        for match in all_matches:
            e_name = match['entity_name']
            if e_name not in entity_best or match['score'] > entity_best[e_name]['score']:
                entity_best[e_name] = match

        final_matches = list(entity_best.values())
        print(f"      去重后: {len(final_matches)} 对")

        # 分数分布
        if final_matches:
            scores = [m['score'] for m in final_matches]
            print(f"      最高分: {max(scores):.4f}")
            print(f"      最低分: {min(scores):.4f}")
            print(f"      平均分: {sum(scores)/len(scores):.4f}")

            for threshold in [0.95, 0.90, 0.85, 0.80]:
                count = sum(1 for s in scores if s >= threshold)
                print(f"      >= {threshold}: {count} 对")

        # 写入 Neo4j
        print("\n[5/5] 写入 Neo4j...")
        created = 0
        errors = 0

        for i in range(0, len(final_matches), 100):
            batch = final_matches[i:i+100]
            c, e = matcher.create_isSameAs_batch(batch)
            created += c
            errors += e
            if (i + 100) % 1000 == 0:
                print(f"      写入进度: {min(i+100, len(final_matches))}/{len(final_matches)}, 成功: {created}")

        print(f"\n      成功创建 {created} 条边")
        if errors > 0:
            print(f"      失败: {errors} 条")

        # 更新实验记录
        matcher.update_fusion_method_record(created, SIMILARITY_THRESHOLD)
        print("      实验记录已更新")

        # 总结
        print("\n" + "=" * 60)
        print("TF-IDF 语义匹配完成！")
        print(f"当前 isSameAs 总数: 4112 + {created} = {4112 + created}")
        print(f"匹配率: {4112 + created}/{len(entities) + 4112} = "
              f"{round(100 * (4112 + created) / (len(entities) + 4112), 1)}%")
        print("=" * 60)

    finally:
        matcher.close()


if __name__ == "__main__":
    run_tfidf_matching()