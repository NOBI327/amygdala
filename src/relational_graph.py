"""RelationalGraphEngine - 感情タグ付き関係性グラフ管理モジュール。

エンティティ（人物・話題・場所等）間の関係性を感情ベクトル付きで
追跡・減衰・検索する。
"""

import json
import logging
import math
from datetime import datetime
from typing import List, Optional, TypedDict

from .config import Config
from .db import DatabaseManager
from .llm_adapter import LLMAdapter

logger = logging.getLogger(__name__)

EMOTION_AXES = ("joy", "sadness", "anger", "fear",
                "surprise", "disgust", "trust", "anticipation")
META_AXES = ("importance", "urgency")
ALL_AXES = EMOTION_AXES + META_AXES

# 敬称除去リスト（Phase 1 同一性解決）
HONORIFIC_SUFFIXES = ("さん", "くん", "ちゃん", "氏", "先生", "様")

# 減衰率カテゴリ
DECAY_RATES = {
    "stable": 0.01,
    "situational": 0.05,
    "temporary": 0.10,
}

ENTITY_EXTRACTION_PROMPT = """以下の対話テキストから、登場するエンティティ（人物・話題・アイテム・場所・出来事）を抽出せよ。

ルール:
- 各エンティティに type を付与: person | topic | item | place | event
- エンティティ間の関係性がある場合、relations で記述
- 関係性にはタグ（関係の性質を表す短いラベル）を付与
- 代名詞（「彼」「それ」等）は可能な限り具体名に解決
- 最大5エンティティまで（重要度順）

出力形式（JSONのみ）:
{{
  "entities": [
    {{
      "label": "上司",
      "type": "person",
      "aliases": ["佐藤部長"],
      "relations": [
        {{"target": "プロジェクトA", "tags": ["担当者", "責任者"]}}
      ]
    }}
  ]
}}

対話テキスト:
{text}
"""


# ----- TypedDict 型定義 -----

class GraphNode(TypedDict):
    id: int
    label: str
    type: str
    aliases: List[str]
    mention_count: int
    base_emotion: dict

class GraphEdge(TypedDict):
    id: int
    source_id: int
    target_id: int
    tags: list
    emotion_vector: dict
    strength: float
    confidence: float
    activation_count: int

class GraphTag(TypedDict):
    id: int
    label: str
    strength: float
    activation_count: int
    confirmed: bool
    decay_rate: float

class EntityContext(TypedDict):
    """Frontmanに渡すサマリー形式"""
    entity: str
    primary_emotion: dict
    active_tags: List[str]
    related_entities: List[str]
    confidence: float


# ----- メインクラス -----

class RelationalGraphEngine:
    """感情タグ付き関係性グラフエンジン。"""

    def __init__(self, config: Config, db_manager: DatabaseManager,
                 llm_adapter: Optional[LLMAdapter] = None):
        self.config = config
        self.db = db_manager
        self.llm = llm_adapter

    # ========== ノード操作 ==========

    def _normalize_label(self, raw_label: str) -> str:
        """基本的な正規化（敬称除去・空白トリム）"""
        label = raw_label.strip()
        for suffix in HONORIFIC_SUFFIXES:
            if label.endswith(suffix) and len(label) > len(suffix):
                label = label[:-len(suffix)]
        return label

    def _merge_base_emotion(self, existing_vec: dict, new_vec: dict,
                            mention_count: int) -> dict:
        """指数移動平均で基底感情を更新"""
        alpha = 2.0 / (mention_count + 1)
        merged = {}
        for axis in ALL_AXES:
            old = existing_vec.get(axis, 0.0)
            new = new_vec.get(axis, 0.0)
            merged[axis] = old * (1 - alpha) + new * alpha
        return merged

    def _row_to_emotion_vec(self, row) -> dict:
        """DBの行から感情ベクトルを抽出"""
        return {axis: float(row[axis]) for axis in ALL_AXES}

    def _row_to_node(self, row) -> GraphNode:
        """DBの行をGraphNodeに変換"""
        aliases = json.loads(row["aliases"]) if row["aliases"] else []
        return GraphNode(
            id=row["id"],
            label=row["label"],
            type=row["type"],
            aliases=aliases,
            mention_count=row["mention_count"],
            base_emotion=self._row_to_emotion_vec(row),
        )

    def upsert_node(self, label: str, type: str, emotion_vec: dict,
                    aliases: Optional[List[str]] = None) -> GraphNode:
        """ノードを作成または更新する。"""
        normalized = self._normalize_label(label)
        aliases = aliases or []
        conn = self.db.get_connection()

        existing = conn.execute(
            "SELECT * FROM graph_nodes WHERE label = ? AND archived = FALSE",
            (normalized,)
        ).fetchone()

        if existing is None:
            # aliases 検索
            existing = self._find_by_alias(normalized)

        if existing:
            new_count = existing["mention_count"] + 1
            merged = self._merge_base_emotion(
                self._row_to_emotion_vec(existing), emotion_vec, new_count
            )
            # aliases をマージ
            old_aliases = json.loads(existing["aliases"]) if existing["aliases"] else []
            merged_aliases = list(set(old_aliases + aliases))

            emotion_updates = ", ".join(f"{axis} = ?" for axis in ALL_AXES)
            sql = f"""UPDATE graph_nodes SET
                mention_count = ?, last_seen = CURRENT_TIMESTAMP,
                aliases = ?, {emotion_updates}
                WHERE id = ?"""
            params = [new_count, json.dumps(merged_aliases, ensure_ascii=False)]
            params.extend(merged[axis] for axis in ALL_AXES)
            params.append(existing["id"])
            conn.execute(sql, params)
            conn.commit()

            updated = conn.execute(
                "SELECT * FROM graph_nodes WHERE id = ?", (existing["id"],)
            ).fetchone()
            return self._row_to_node(updated)
        else:
            emotion_cols = ", ".join(ALL_AXES)
            placeholders = ", ".join("?" for _ in ALL_AXES)
            sql = f"""INSERT INTO graph_nodes (label, type, aliases, {emotion_cols})
                VALUES (?, ?, ?, {placeholders})"""
            params = [normalized, type, json.dumps(aliases, ensure_ascii=False)]
            params.extend(emotion_vec.get(axis, 0.0) for axis in ALL_AXES)
            conn.execute(sql, params)
            conn.commit()

            row = conn.execute(
                "SELECT * FROM graph_nodes WHERE label = ?", (normalized,)
            ).fetchone()
            return self._row_to_node(row)

    def find_node(self, label_or_alias: str) -> Optional[GraphNode]:
        """ラベルまたはaliasでノードを検索"""
        normalized = self._normalize_label(label_or_alias)
        conn = self.db.get_connection()

        row = conn.execute(
            "SELECT * FROM graph_nodes WHERE label = ? AND archived = FALSE",
            (normalized,)
        ).fetchone()
        if row:
            return self._row_to_node(row)

        found = self._find_by_alias(normalized)
        if found:
            return self._row_to_node(found)
        return None

    def _find_by_alias(self, normalized_label: str):
        """aliasesカラム内を検索して一致するノード行を返す"""
        conn = self.db.get_connection()
        rows = conn.execute(
            "SELECT * FROM graph_nodes WHERE archived = FALSE"
        ).fetchall()
        for row in rows:
            aliases = json.loads(row["aliases"]) if row["aliases"] else []
            normalized_aliases = [self._normalize_label(a) for a in aliases]
            if normalized_label in normalized_aliases:
                return row
        return None

    # ========== エッジ操作 ==========

    def _row_to_edge(self, row) -> GraphEdge:
        """DBの行をGraphEdgeに変換"""
        conn = self.db.get_connection()
        tag_rows = conn.execute(
            "SELECT * FROM graph_tags WHERE edge_id = ?", (row["id"],)
        ).fetchall()
        tags = [self._row_to_tag(t) for t in tag_rows]

        return GraphEdge(
            id=row["id"],
            source_id=row["source_id"],
            target_id=row["target_id"],
            tags=tags,
            emotion_vector=self._row_to_emotion_vec(row),
            strength=float(row["strength"]),
            confidence=float(row["confidence"]),
            activation_count=row["activation_count"],
        )

    def upsert_edge(self, source_label: str, target_label: str,
                    emotion_vec: dict,
                    tag_labels: Optional[List[str]] = None) -> GraphEdge:
        """エッジを作成または更新する。"""
        source = self.find_node(source_label)
        target = self.find_node(target_label)
        if not source or not target:
            raise ValueError(
                f"Source or target node not found: {source_label!r}, {target_label!r}"
            )
        tag_labels = tag_labels or []
        conn = self.db.get_connection()

        existing = conn.execute(
            "SELECT * FROM graph_edges WHERE source_id = ? AND target_id = ? AND archived = FALSE",
            (source["id"], target["id"])
        ).fetchone()

        if existing:
            new_count = existing["activation_count"] + 1
            merged = self._merge_base_emotion(
                self._row_to_emotion_vec(existing), emotion_vec, new_count
            )
            # strength を補強（上限 5.0）
            new_strength = min(existing["strength"] + 0.2, 5.0)
            # confidence を上げる（上限 1.0）
            new_confidence = min(existing["confidence"] + 0.1, 1.0)

            emotion_updates = ", ".join(f"{axis} = ?" for axis in ALL_AXES)
            sql = f"""UPDATE graph_edges SET
                activation_count = ?, strength = ?, confidence = ?,
                last_activated = CURRENT_TIMESTAMP,
                {emotion_updates}
                WHERE id = ?"""
            params = [new_count, new_strength, new_confidence]
            params.extend(merged[axis] for axis in ALL_AXES)
            params.append(existing["id"])
            conn.execute(sql, params)
            conn.commit()
            edge_id = existing["id"]
        else:
            emotion_cols = ", ".join(ALL_AXES)
            placeholders = ", ".join("?" for _ in ALL_AXES)
            sql = f"""INSERT INTO graph_edges (source_id, target_id, {emotion_cols})
                VALUES (?, ?, {placeholders})"""
            params = [source["id"], target["id"]]
            params.extend(emotion_vec.get(axis, 0.0) for axis in ALL_AXES)
            conn.execute(sql, params)
            conn.commit()
            edge_id = conn.execute(
                "SELECT id FROM graph_edges WHERE source_id = ? AND target_id = ?",
                (source["id"], target["id"])
            ).fetchone()["id"]

        # タグを upsert
        for tag_label in tag_labels:
            self.upsert_tag(edge_id, tag_label, source_type=source["type"],
                            target_type=target["type"], emotion_vec=emotion_vec)

        row = conn.execute(
            "SELECT * FROM graph_edges WHERE id = ?", (edge_id,)
        ).fetchone()
        return self._row_to_edge(row)

    def get_edges(self, node_id: int, include_archived: bool = False) -> List[GraphEdge]:
        """指定ノードに接続するエッジを取得"""
        conn = self.db.get_connection()
        if include_archived:
            rows = conn.execute(
                "SELECT * FROM graph_edges WHERE source_id = ? OR target_id = ?",
                (node_id, node_id)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM graph_edges WHERE (source_id = ? OR target_id = ?) AND archived = FALSE",
                (node_id, node_id)
            ).fetchall()
        return [self._row_to_edge(r) for r in rows]

    # ========== タグ操作 ==========

    def _row_to_tag(self, row) -> GraphTag:
        return GraphTag(
            id=row["id"],
            label=row["label"],
            strength=float(row["strength"]),
            activation_count=row["activation_count"],
            confirmed=bool(row["confirmed"]),
            decay_rate=float(row["decay_rate"]),
        )

    def _determine_decay_rate(self, source_type: str, emotion_vec: dict,
                              target_type: str = "") -> float:
        """ヒューリスティクスで減衰率を自動判定。

        source_type/target_type いずれかが item/place なら temporary。
        両方 person で肯定感情優位なら stable。それ以外は situational。
        """
        types = {source_type, target_type}
        if types & {"item", "place"}:
            return DECAY_RATES["temporary"]
        if "person" in types:
            positive = emotion_vec.get("trust", 0) + emotion_vec.get("joy", 0)
            negative = emotion_vec.get("anger", 0) + emotion_vec.get("fear", 0)
            if positive > negative:
                return DECAY_RATES["stable"]
            return DECAY_RATES["situational"]
        return DECAY_RATES["situational"]

    def upsert_tag(self, edge_id: int, label: str,
                   source_type: str = "", target_type: str = "",
                   emotion_vec: Optional[dict] = None) -> GraphTag:
        """タグを作成または更新する。"""
        conn = self.db.get_connection()
        existing = conn.execute(
            "SELECT * FROM graph_tags WHERE edge_id = ? AND label = ?",
            (edge_id, label)
        ).fetchone()

        if existing:
            new_count = existing["activation_count"] + 1
            new_strength = min(existing["strength"] + 0.1, 1.0)
            conn.execute(
                """UPDATE graph_tags SET
                    activation_count = ?, strength = ?,
                    last_activated = CURRENT_TIMESTAMP
                    WHERE id = ?""",
                (new_count, new_strength, existing["id"])
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM graph_tags WHERE id = ?", (existing["id"],)
            ).fetchone()
        else:
            decay_rate = self._determine_decay_rate(
                source_type, emotion_vec or {}, target_type
            )
            conn.execute(
                """INSERT INTO graph_tags (edge_id, label, decay_rate)
                    VALUES (?, ?, ?)""",
                (edge_id, label, decay_rate)
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM graph_tags WHERE edge_id = ? AND label = ?",
                (edge_id, label)
            ).fetchone()

        return self._row_to_tag(row)

    def confirm_tag(self, tag_id: int) -> None:
        """タグを昇格（confirmed=True, strength=1.0）"""
        conn = self.db.get_connection()
        conn.execute(
            "UPDATE graph_tags SET confirmed = TRUE, strength = 1.0 WHERE id = ?",
            (tag_id,)
        )
        conn.commit()

    def _auto_confirm_tags(self) -> int:
        """activation_count >= 閾値のタグを一括昇格"""
        conn = self.db.get_connection()
        threshold = self.config.TAG_CANDIDATE_THRESHOLD
        cursor = conn.execute(
            """UPDATE graph_tags SET confirmed = TRUE, strength = 1.0
               WHERE confirmed = FALSE AND activation_count >= ?""",
            (threshold,)
        )
        conn.commit()
        return cursor.rowcount

    # ========== 減衰処理 ==========

    def _compute_tag_strength(self, tag_row) -> float:
        """現在の減衰後タグ強度を計算"""
        last_activated = tag_row["last_activated"]
        if isinstance(last_activated, str):
            last_activated = datetime.fromisoformat(last_activated)
        days = (datetime.now() - last_activated).total_seconds() / 86400
        return float(tag_row["strength"]) * math.exp(
            -float(tag_row["decay_rate"]) * days
        )

    def apply_decay(self) -> dict:
        """全タグに減衰を適用し、閾値以下を削除、タグなしエッジをarchive"""
        conn = self.db.get_connection()
        threshold = self.config.TAG_STRENGTH_THRESHOLD

        all_tags = conn.execute("SELECT * FROM graph_tags").fetchall()
        decayed = 0
        removed = 0

        for tag in all_tags:
            new_strength = self._compute_tag_strength(tag)
            if new_strength < threshold:
                conn.execute("DELETE FROM graph_tags WHERE id = ?", (tag["id"],))
                removed += 1
            else:
                conn.execute(
                    "UPDATE graph_tags SET strength = ? WHERE id = ?",
                    (new_strength, tag["id"])
                )
                decayed += 1

        # タグが全削除されたエッジを soft-archive
        archived_edges = 0
        edges = conn.execute(
            "SELECT id FROM graph_edges WHERE archived = FALSE"
        ).fetchall()
        for edge in edges:
            tag_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM graph_tags WHERE edge_id = ?",
                (edge["id"],)
            ).fetchone()["cnt"]
            if tag_count == 0:
                conn.execute(
                    "UPDATE graph_edges SET archived = TRUE WHERE id = ?",
                    (edge["id"],)
                )
                archived_edges += 1

        conn.commit()
        return {
            "decayed_tags": decayed,
            "removed_tags": removed,
            "archived_edges": archived_edges,
        }

    # ========== 検索 ==========

    def get_entity_context(self, label: str, hops: int = 1) -> Optional[EntityContext]:
        """エンティティの関係性コンテキストを返す"""
        node = self.find_node(label)
        if not node:
            return None

        hops = min(hops, self.config.GRAPH_HOP_LIMIT)
        edges = self.get_edges(node["id"])

        # 主要感情を算出
        emo = node["base_emotion"]
        primary = {k: v for k, v in sorted(
            emo.items(), key=lambda x: x[1], reverse=True
        )[:3] if v > 0}

        # 関連エンティティ
        conn = self.db.get_connection()
        related = []
        active_tags = []
        for edge in edges:
            other_id = (edge["target_id"]
                        if edge["source_id"] == node["id"]
                        else edge["source_id"])
            other = conn.execute(
                "SELECT label FROM graph_nodes WHERE id = ?", (other_id,)
            ).fetchone()
            if other:
                related.append(other["label"])
            for tag in edge["tags"]:
                if tag["confirmed"]:
                    active_tags.append(tag["label"])

        # 2ホップ目
        if hops >= 2:
            for edge in edges:
                other_id = (edge["target_id"]
                            if edge["source_id"] == node["id"]
                            else edge["source_id"])
                hop2_edges = self.get_edges(other_id)
                for h2e in hop2_edges:
                    far_id = (h2e["target_id"]
                              if h2e["source_id"] == other_id
                              else h2e["source_id"])
                    if far_id != node["id"]:
                        far = conn.execute(
                            "SELECT label FROM graph_nodes WHERE id = ?",
                            (far_id,)
                        ).fetchone()
                        if far and far["label"] not in related:
                            related.append(far["label"])

        avg_confidence = (
            sum(e["confidence"] for e in edges) / len(edges)
            if edges else 0.0
        )

        return EntityContext(
            entity=node["label"],
            primary_emotion=primary,
            active_tags=list(set(active_tags)),
            related_entities=related,
            confidence=avg_confidence,
        )

    def search_by_tag(self, tag_label: str) -> List[GraphEdge]:
        """タグラベルでエッジを検索"""
        conn = self.db.get_connection()
        tag_rows = conn.execute(
            "SELECT edge_id FROM graph_tags WHERE label = ?", (tag_label,)
        ).fetchall()
        edges = []
        for t in tag_rows:
            row = conn.execute(
                "SELECT * FROM graph_edges WHERE id = ? AND archived = FALSE",
                (t["edge_id"],)
            ).fetchone()
            if row:
                edges.append(self._row_to_edge(row))
        return edges

    def search_by_emotion(self, emotion_vec: dict, top_k: int = 5) -> List[GraphNode]:
        """感情ベクトルの類似度でノードを検索"""
        conn = self.db.get_connection()
        rows = conn.execute(
            "SELECT * FROM graph_nodes WHERE archived = FALSE"
        ).fetchall()

        def cosine_sim(a: dict, b: dict) -> float:
            dot = sum(a.get(ax, 0) * b.get(ax, 0) for ax in ALL_AXES)
            mag_a = math.sqrt(sum(a.get(ax, 0) ** 2 for ax in ALL_AXES))
            mag_b = math.sqrt(sum(b.get(ax, 0) ** 2 for ax in ALL_AXES))
            if mag_a == 0 or mag_b == 0:
                return 0.0
            return dot / (mag_a * mag_b)

        scored = []
        for row in rows:
            node_emo = self._row_to_emotion_vec(row)
            sim = cosine_sim(emotion_vec, node_emo)
            scored.append((sim, row))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [self._row_to_node(row) for _, row in scored[:top_k]]

    # ========== エンティティ抽出（LLM依存） ==========

    def extract_entities(self, text: str, emotion_vec: dict) -> List[dict]:
        """LLMでテキストからエンティティを抽出"""
        if not self.llm:
            return []

        prompt = ENTITY_EXTRACTION_PROMPT.format(text=text)
        try:
            response = self.llm.generate(prompt)
            # JSON部分を抽出
            response = response.strip()
            if response.startswith("```"):
                lines = response.split("\n")
                response = "\n".join(lines[1:-1])
            data = json.loads(response)
            return data.get("entities", [])
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Entity extraction failed: {e}")
            return []

    # ========== スコープ管理 ==========

    def _enforce_node_limit(self) -> int:
        """ノード上限を超過した分を soft-archive"""
        conn = self.db.get_connection()
        limit = self.config.GRAPH_MAX_ACTIVE_NODES
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM graph_nodes WHERE archived = FALSE"
        ).fetchone()["cnt"]

        if count <= limit:
            return 0

        excess = count - limit
        # importance × mention_count × recency で昇順ソート → 低いものから archive
        rows = conn.execute("""
            SELECT id, mention_count, importance, last_seen
            FROM graph_nodes WHERE archived = FALSE
            ORDER BY (importance * mention_count) ASC
            LIMIT ?
        """, (excess,)).fetchall()

        for row in rows:
            conn.execute(
                "UPDATE graph_nodes SET archived = TRUE WHERE id = ?",
                (row["id"],)
            )
        conn.commit()
        return len(rows)

    def _enforce_edge_limit(self, node_id: int) -> int:
        """特定ノードのエッジ上限を超過した分を soft-archive"""
        conn = self.db.get_connection()
        limit = self.config.GRAPH_MAX_EDGES_PER_NODE
        rows = conn.execute(
            """SELECT * FROM graph_edges
               WHERE (source_id = ? OR target_id = ?) AND archived = FALSE
               ORDER BY (strength * confidence) ASC""",
            (node_id, node_id)
        ).fetchall()

        if len(rows) <= limit:
            return 0

        excess = len(rows) - limit
        archived = 0
        for row in rows[:excess]:
            conn.execute(
                "UPDATE graph_edges SET archived = TRUE WHERE id = ?",
                (row["id"],)
            )
            archived += 1
        conn.commit()
        return archived

    def _enforce_tag_limit(self, edge_id: int) -> int:
        """特定エッジのタグ上限を超過した分を削除"""
        conn = self.db.get_connection()
        limit = self.config.GRAPH_MAX_TAGS_PER_EDGE
        rows = conn.execute(
            "SELECT * FROM graph_tags WHERE edge_id = ? ORDER BY strength ASC",
            (edge_id,)
        ).fetchall()

        if len(rows) <= limit:
            return 0

        excess = len(rows) - limit
        removed = 0
        for row in rows[:excess]:
            conn.execute("DELETE FROM graph_tags WHERE id = ?", (row["id"],))
            removed += 1
        conn.commit()
        return removed

    # ========== 統合エントリポイント ==========

    def process_turn(self, text: str, emotion_vec: dict, entities: list | None = None) -> dict:
        """テキストからエンティティ抽出→グラフ更新→減衰→制約適用の一連フロー

        Args:
            text: 対話テキスト
            emotion_vec: 感情ベクトル
            entities: 外部から渡すエンティティリスト（省略時は内部LLMで抽出）
        """
        if entities is None:
            entities = self.extract_entities(text, emotion_vec)
        if not entities:
            return {"updated": False}

        nodes_affected = set()
        edges_affected = 0

        for ent in entities:
            label = ent.get("label", "")
            ent_type = ent.get("type", "topic")
            aliases = ent.get("aliases", [])

            if not label:
                continue

            normalized = self._normalize_label(label)
            self.upsert_node(label, ent_type, emotion_vec, aliases)
            nodes_affected.add(normalized)

            for rel in ent.get("relations", []):
                target = rel.get("target", "")
                tags = rel.get("tags", [])
                if not target:
                    continue
                target_normalized = self._normalize_label(target)
                # target ノードが存在しない場合は作成
                if not self.find_node(target):
                    self.upsert_node(target, "topic", emotion_vec)
                    nodes_affected.add(target_normalized)
                self.upsert_edge(label, target, emotion_vec, tags)
                edges_affected += 1

        tags_confirmed = self._auto_confirm_tags()
        decay_result = self.apply_decay()
        self._enforce_node_limit()

        return {
            "updated": True,
            "nodes_affected": len(nodes_affected),
            "edges_affected": edges_affected,
            "tags_confirmed": tags_confirmed,
            "tags_decayed": decay_result["removed_tags"],
        }

    # ========== 手動マージ ==========

    def merge_nodes(self, keep_id: int, merge_id: int) -> GraphNode:
        """merge_id のノードを keep_id に統合"""
        conn = self.db.get_connection()

        keep = conn.execute(
            "SELECT * FROM graph_nodes WHERE id = ?", (keep_id,)
        ).fetchone()
        merge = conn.execute(
            "SELECT * FROM graph_nodes WHERE id = ?", (merge_id,)
        ).fetchone()
        if not keep or not merge:
            raise ValueError(f"Node not found: keep={keep_id}, merge={merge_id}")

        # aliases をマージ
        keep_aliases = json.loads(keep["aliases"]) if keep["aliases"] else []
        merge_aliases = json.loads(merge["aliases"]) if merge["aliases"] else []
        merged_aliases = list(set(
            keep_aliases + merge_aliases + [merge["label"]]
        ))

        # mention_count 合算
        total_count = keep["mention_count"] + merge["mention_count"]

        # base_emotion を加重平均
        keep_emo = self._row_to_emotion_vec(keep)
        merge_emo = self._row_to_emotion_vec(merge)
        w_keep = keep["mention_count"] / total_count
        w_merge = merge["mention_count"] / total_count
        merged_emo = {
            axis: keep_emo[axis] * w_keep + merge_emo[axis] * w_merge
            for axis in ALL_AXES
        }

        # keep ノード更新
        emotion_updates = ", ".join(f"{axis} = ?" for axis in ALL_AXES)
        sql = f"""UPDATE graph_nodes SET
            mention_count = ?, aliases = ?, {emotion_updates}
            WHERE id = ?"""
        params = [total_count, json.dumps(merged_aliases, ensure_ascii=False)]
        params.extend(merged_emo[axis] for axis in ALL_AXES)
        params.append(keep_id)
        conn.execute(sql, params)

        # merge_id のエッジを keep_id に付け替え
        conn.execute(
            "UPDATE graph_edges SET source_id = ? WHERE source_id = ?",
            (keep_id, merge_id)
        )
        conn.execute(
            "UPDATE graph_edges SET target_id = ? WHERE target_id = ?",
            (keep_id, merge_id)
        )

        # 付け替え後に重複エッジが生じた場合の処理（UNIQUE制約違反を回避）
        # → 重複は低 strength 側を archive
        edges = conn.execute(
            """SELECT source_id, target_id, COUNT(*) as cnt
               FROM graph_edges WHERE archived = FALSE
               GROUP BY source_id, target_id HAVING cnt > 1"""
        ).fetchall()
        for dup in edges:
            dups = conn.execute(
                """SELECT id, strength FROM graph_edges
                   WHERE source_id = ? AND target_id = ? AND archived = FALSE
                   ORDER BY strength DESC""",
                (dup["source_id"], dup["target_id"])
            ).fetchall()
            for d in dups[1:]:  # 最強以外を archive
                conn.execute(
                    "UPDATE graph_edges SET archived = TRUE WHERE id = ?",
                    (d["id"],)
                )

        # merge_id を soft-archive
        conn.execute(
            "UPDATE graph_nodes SET archived = TRUE WHERE id = ?", (merge_id,)
        )
        conn.commit()

        updated = conn.execute(
            "SELECT * FROM graph_nodes WHERE id = ?", (keep_id,)
        ).fetchone()
        return self._row_to_node(updated)
