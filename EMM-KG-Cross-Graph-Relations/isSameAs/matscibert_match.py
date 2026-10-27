"""
MatSciBERT 语义匹配 - V2（阈值0.95 + 清理Word2Vec后重跑）
"""

import os
import numpy as np
import re
import torch
from transformers import AutoTokenizer, AutoModel
from sklearn.metrics.pairwise import cosine_similarity
from neo4j import GraphDatabase
import datetime

# ============ 配置 ============
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = "testcypher1.db"

MODEL_PATH = "D:/models/matscibert"
SIMILARITY_THRESHOLD = 0.95
BATCH_SIZE = 32
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

MIN_LENGTH_RATIO = 0.4
MAX_LENGTH_RATIO = 2.5


def is_valid_name(name):
    if not name or len(name.strip()) < 2:
        return False
    if re.match(r'^[\d\s\.\,\;\:\#\%\$\@\!\?\*\(\)\[\]\{\}\-\+\=]+$', name):
        return False
    return True


def extract_tokens(name):
    tokens = set()
    chem_tokens = re.findall(r'[A-Z][a-z]?\d*', name)
    for t in chem_tokens:
        if len(t) >= 2:
            tokens.add(t.lower())
    words = re.findall(r'[a-zA-Z]{2,}', name.lower())
    for w in words:
        tokens.add(w)
    return tokens


def check_shared_tokens(name1, name2, min_shared=1):
    t1 = extract_tokens(name1)
    t2 = extract_tokens(name2)
    stop_words = {'the', 'of', 'and', 'in', 'on', 'at', 'to', 'for', 'with',
                  'by', 'from', 'as', 'is', 'was', 'are', 'were', 'be', 'been'}
    shared = (t1 & t2) - stop_words
    return len(shared) >= min_shared


def check_length_ratio(name1, name2):
    if not name1 or not name2:
        return False
    ratio = len(name1) / max(len(name2), 1)
    return MIN_LENGTH_RATIO <= ratio <= MAX_LENGTH_RATIO


def mean_pooling(model_output, attention_mask):
    token_embeddings = model_output[0]
    input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
    return torch.sum(token_embeddings * input_mask_expanded, 1) / torch.clamp(
        input_mask_expanded.sum(1), min=1e-9)


class Neo4jMatcher:
    def __init__(self, uri, user, password, database):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database

    def close(self):
        self.driver.close()

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
                            r.method = 'matscibert_cosine_v2',
                            r.confidence = $confidence,
                            r.similarity_score = $score,
                            r.model = 'MatSciBERT',
                            r.shared_tokens = $shared,
                            r.created_at = datetime()
                        RETURN count(r) as created
                    """,
                                         entity_name=match['entity_name'],
                                         term_name=match['term_name'],
                                         confidence=round(match['score'], 4),
                                         score=round(match['score'], 4),
                                         shared=match.get('shared_tokens', 0))
                    record = result.single()
                    if record and record['created']:
                        created += 1
                except:
                    pass
            return created

    def update_fusion_method_record(self, count, threshold):
        with self.driver.session(database=self.database) as session:
            session.run("""
                MERGE (fm:FusionMethod {method_id: 'isSameAs_matscibert_cosine_v2'})
                SET fm.relation_type = 'isSameAs',
                    fm.name = 'MatSciBERT语义匹配V2',
                    fm.description = '阈值0.95+共享词元+长度比约束，Word2Vec清理后重跑',
                    fm.confidence_threshold = $threshold,
                    fm.match_count = $count,
                    fm.model = 'MatSciBERT (local)',
                    fm.quality_level = 'B',
                    fm.status = 'verified',
                    fm.updated_at = datetime()
            """, threshold=threshold, count=count)


def encode_texts(model, tokenizer, texts, batch_size):
    all_embeddings = []
    total = len(texts)

    for i in range(0, total, batch_size):
        batch_texts = texts[i:i + batch_size]
        encoded = tokenizer(
            batch_texts,
            padding=True,
            truncation=True,
            max_length=128,
            return_tensors='pt'
        ).to(DEVICE)

        with torch.no_grad():
            outputs = model(**encoded)
            embeddings = mean_pooling(outputs, encoded['attention_mask'])
            all_embeddings.append(embeddings.cpu().numpy())

        if (i + batch_size) % 5000 == 0 or (i + batch_size) >= total:
            print(f"      编码: {min(i + batch_size, total)}/{total}")

    return np.vstack(all_embeddings)


def run_matscibert_matching_v2():
    print("=" * 60)
    print("MatSciBERT 语义匹配 V2（阈值0.95 + 清理后重跑）")
    print(f"模型路径: {MODEL_PATH}")
    print(f"设备: {DEVICE}")
    print(f"相似度阈值: {SIMILARITY_THRESHOLD}")
    print("=" * 60)

    print("\n[1/6] 加载 MatSciBERT 模型...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
    model = AutoModel.from_pretrained(MODEL_PATH).to(DEVICE)
    model.eval()
    print("      模型加载完成！")

    matcher = Neo4jMatcher(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE)

    try:
        print("\n[2/6] 获取数据...")
        entities = matcher.get_unmatched_entities()
        mged_terms = matcher.get_all_mged_terms()

        entities = [e for e in entities if is_valid_name(e['cleanName'])]
        mged_terms = [t for t in mged_terms if is_valid_name(t['cleanName'])]

        print(f"      有效未匹配 Entity: {len(entities)}")
        print(f"      有效 MGED 术语: {len(mged_terms)}")

        entity_names = [e['cleanName'] for e in entities]
        term_names = [t['cleanName'] for t in mged_terms]

        print(f"\n[3/6] 编码 {len(entity_names)} 个实体...")
        entity_embeddings = encode_texts(model, tokenizer, entity_names, BATCH_SIZE)

        print(f"\n[4/6] 编码 {len(term_names)} 个 MGED 术语...")
        term_embeddings = encode_texts(model, tokenizer, term_names, BATCH_SIZE)

        print(f"\n[5/6] 计算相似度并匹配（阈值={SIMILARITY_THRESHOLD}）...")
        matches = []
        query_batch = 1000

        for i in range(0, len(entity_embeddings), query_batch):
            end = min(i + query_batch, len(entity_embeddings))
            e_batch = entity_embeddings[i:end]
            sim_matrix = cosine_similarity(e_batch, term_embeddings)

            for j in range(sim_matrix.shape[0]):
                top_indices = np.argsort(sim_matrix[j])[-10:][::-1]
                best_match = None
                best_score = 0

                for idx in top_indices:
                    score = sim_matrix[j][idx]
                    if score < SIMILARITY_THRESHOLD:
                        continue

                    entity = entities[i + j]
                    term = mged_terms[idx]

                    if not check_length_ratio(entity['cleanName'], term['cleanName']):
                        continue
                    if not check_shared_tokens(entity['cleanName'], term['cleanName']):
                        continue

                    if score > best_score:
                        best_score = score
                        best_match = term

                if best_match:
                    matches.append({
                        'entity_name': entities[i + j]['name'],
                        'term_name': best_match['name'],
                        'score': float(best_score),
                        'shared_tokens': len(extract_tokens(entities[i + j]['cleanName']) &
                                             extract_tokens(best_match['cleanName']))
                    })

            if end % 5000 == 0 or end == len(entity_embeddings):
                print(f"      进度: {end}/{len(entity_embeddings)}, 已匹配: {len(matches)}")

        print(f"\n      匹配结果: {len(matches)} 对")

        if matches:
            scores = [m['score'] for m in matches]
            print(f"      最高分: {max(scores):.4f}")
            print(f"      最低分: {min(scores):.4f}")
            print(f"      平均分: {sum(scores) / len(scores):.4f}")
            for t in [0.98, 0.95]:
                c = sum(1 for s in scores if s >= t)
                print(f"      >= {t}: {c}")

        print(f"\n[6/6] 写入 Neo4j...")
        created = 0
        for i in range(0, len(matches), 100):
            batch = matches[i:i + 100]
            created += matcher.create_isSameAs_batch(batch)
            if (i + 100) % 1000 == 0:
                print(f"      写入: {min(i + 100, len(matches))}/{len(matches)}, 成功: {created}")

        print(f"\n      成功创建 {created} 条边")
        matcher.update_fusion_method_record(created, SIMILARITY_THRESHOLD)

        print("\n" + "=" * 60)
        print("MatSciBERT V2 匹配完成！")
        print(f"新增 {created} 条匹配")
        print("=" * 60)

    finally:
        matcher.close()


if __name__ == "__main__":
    run_matscibert_matching_v2()