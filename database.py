"""
SQLite 数据库模块
用于存储和查询成绩历史记录
"""
import sqlite3
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Grade:
    """成绩数据类"""
    course_id: str        # 课程代码
    course_name: str      # 课程名称
    score: str            # 成绩（可能是数字或等级）
    semester: str         # 学期
    grade_point: Optional[float] = None  # 绩点
    first_seen: Optional[datetime] = None  # 首次发现时间


class GradeDatabase:
    """成绩数据库管理类"""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """初始化数据库表"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS grades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_id TEXT NOT NULL,
                    course_name TEXT NOT NULL,
                    score TEXT NOT NULL,
                    semester TEXT NOT NULL,
                    grade_point REAL,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(course_id, semester)
                )
            ''')
            conn.commit()
    
    def is_new_grade(self, grade: Grade) -> bool:
        """检查是否为新成绩"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                'SELECT 1 FROM grades WHERE course_id = ? AND semester = ?',
                (grade.course_id, grade.semester)
            )
            return cursor.fetchone() is None
    
    def save_grade(self, grade: Grade) -> bool:
        """
        保存成绩到数据库
        返回 True 表示是新成绩，False 表示已存在
        """
        if not self.is_new_grade(grade):
            return False
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT INTO grades (course_id, course_name, score, semester, grade_point)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                grade.course_id,
                grade.course_name,
                grade.score,
                grade.semester,
                grade.grade_point
            ))
            conn.commit()
        return True
    
    def get_all_grades(self, semester: Optional[str] = None) -> list[Grade]:
        """获取所有成绩，可按学期筛选"""
        with sqlite3.connect(self.db_path) as conn:
            if semester:
                cursor = conn.execute(
                    'SELECT course_id, course_name, score, semester, grade_point, first_seen '
                    'FROM grades WHERE semester = ? ORDER BY first_seen DESC',
                    (semester,)
                )
            else:
                cursor = conn.execute(
                    'SELECT course_id, course_name, score, semester, grade_point, first_seen '
                    'FROM grades ORDER BY first_seen DESC'
                )
            
            grades = []
            for row in cursor.fetchall():
                grades.append(Grade(
                    course_id=row[0],
                    course_name=row[1],
                    score=row[2],
                    semester=row[3],
                    grade_point=row[4],
                    first_seen=datetime.fromisoformat(row[5]) if row[5] else None
                ))
            return grades
    
    def get_grade_count(self) -> int:
        """获取总成绩数量"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('SELECT COUNT(*) FROM grades')
            return cursor.fetchone()[0]


@dataclass
class GpaRanking:
    """个人绩点排名数据类"""
    major: str              # 专业名称
    gpa: float              # 绩点
    ranking: int            # 排名
    total_students: int     # 总人数
    percentile: float       # 百分位 (排名/总人数)
    timestamp: Optional[datetime] = None  # 记录时间


@dataclass
class MajorStudentRanking:
    """专业全员榜单中的单条记录"""
    rank: int
    name: str               # 通常是脱敏后的，如 ****
    gpa: float
    credits: float
    major: str
    semester: str = ""      # 学期标识
    is_me: bool = False     # 是否是当前用户


@dataclass
class GradeInference:
    """推断的成绩信息"""
    rank: int                           # 排名
    name: str                           # 学生姓名（脱敏）
    old_gpa: float                      # 旧绩点
    new_gpa: float                      # 新绩点
    old_credits: float                  # 旧学分
    new_credits: float                  # 新学分
    inferred_gpa: Optional[float]       # 推断的新课程绩点
    inferred_credits: float             # 推断的新课程学分
    major: str                          # 专业
    semester: str                       # 学期
    timestamp: Optional[datetime] = None  # 推断时间
    is_pnp: bool = False                # 是否为 P/NP 课程（推断）
    
    @classmethod
    def from_change(cls, old: 'MajorStudentRanking', new: 'MajorStudentRanking', semester: str, infer_pnp: bool = True) -> Optional['GradeInference']:
        """
        从两次快照的变化推断成绩
        
        Args:
            old: 旧的排名数据
            new: 新的排名数据
            semester: 学期
            infer_pnp: 是否启用 P/NP 课程自动识别（默认 True）
                      设为 False 可避免"新课程绩点恰好等于原绩点"的情况被误判为 P/NP
        """
        credit_diff = new.credits - old.credits
        
        # 学分无变化，无法推断
        if abs(credit_diff) < 0.01:
            return None
        
        # 计算推断绩点
        # 公式: new_gpa * new_credits - old_gpa * old_credits = inferred_gpa * credit_diff
        old_weighted = old.gpa * old.credits
        new_weighted = new.gpa * new.credits
        weighted_diff = new_weighted - old_weighted
        
        inferred_gpa = weighted_diff / credit_diff if credit_diff > 0 else None
        
        # P/NP 课程识别逻辑（可通过 infer_pnp 参数关闭）
        is_pnp = False
        if infer_pnp and inferred_gpa is not None:
            # 推断绩点接近 0（阈值 0.1）且学分增加
            # 注意：这可能误判"新课程绩点恰好等于原累计绩点"的情况
            if abs(inferred_gpa) < 0.1 and credit_diff > 0:
                is_pnp = True
            # 推断绩点为负数（理论上不可能，说明是 P/NP 干扰）
            elif inferred_gpa < 0:
                is_pnp = True
                inferred_gpa = None  # 无法计算有效绩点
        
        return cls(
            rank=new.rank,
            name=new.name,
            old_gpa=old.gpa,
            new_gpa=new.gpa,
            old_credits=old.credits,
            new_credits=new.credits,
            inferred_gpa=inferred_gpa,
            inferred_credits=credit_diff,
            major=new.major,
            semester=semester,
            timestamp=datetime.now(),
            is_pnp=is_pnp
        )


class RankingDatabase:
    """排名数据库管理类"""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """初始化排名表"""
        with sqlite3.connect(self.db_path) as conn:
            # 个人排名记录
            conn.execute('''
                CREATE TABLE IF NOT EXISTS rankings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    major TEXT NOT NULL,
                    gpa REAL NOT NULL,
                    ranking INTEGER NOT NULL,
                    total_students INTEGER NOT NULL,
                    percentile REAL NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # 全专业排名快照表 (含学期字段)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS major_rankings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    gpa REAL NOT NULL,
                    credits REAL NOT NULL,
                    major TEXT NOT NULL,
                    semester TEXT DEFAULT '',
                    is_me INTEGER DEFAULT 0,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # 成绩推断记录表
            conn.execute('''
                CREATE TABLE IF NOT EXISTS grade_inferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rank INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    old_gpa REAL NOT NULL,
                    new_gpa REAL NOT NULL,
                    old_credits REAL NOT NULL,
                    new_credits REAL NOT NULL,
                    inferred_gpa REAL,
                    inferred_credits REAL NOT NULL,
                    major TEXT NOT NULL,
                    semester TEXT NOT NULL,
                    is_pnp INTEGER DEFAULT 0,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # 尝试添加 semester 列（如果不存在）
            try:
                conn.execute('ALTER TABLE major_rankings ADD COLUMN semester TEXT DEFAULT ""')
            except sqlite3.OperationalError:
                pass  # 列已存在
            # 尝试添加 is_pnp 列（如果不存在）
            try:
                conn.execute('ALTER TABLE grade_inferences ADD COLUMN is_pnp INTEGER DEFAULT 0')
            except sqlite3.OperationalError:
                pass  # 列已存在
            conn.commit()
    
    def get_latest_ranking(self, major: str) -> Optional[GpaRanking]:
        """获取指定专业的最新排名"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                '''SELECT major, gpa, ranking, total_students, percentile, timestamp 
                   FROM rankings WHERE major = ? ORDER BY timestamp DESC LIMIT 1''',
                (major,)
            )
            row = cursor.fetchone()
            if row:
                return GpaRanking(
                    major=row[0],
                    gpa=row[1],
                    ranking=row[2],
                    total_students=row[3],
                    percentile=row[4],
                    timestamp=datetime.fromisoformat(row[5]) if row[5] else None
                )
            return None
    
    def save_ranking(self, ranking: GpaRanking) -> bool:
        """
        保存排名记录
        返回 True 表示排名有变化，False 表示无变化
        """
        latest = self.get_latest_ranking(ranking.major)
        
        # 检查是否有变化
        has_changed = False
        if latest is None:
            has_changed = True  # 首次记录
        elif latest.ranking != ranking.ranking or latest.gpa != ranking.gpa:
            has_changed = True  # 排名或绩点变化
        
        # 保存新记录
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT INTO rankings (major, gpa, ranking, total_students, percentile)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                ranking.major,
                ranking.gpa,
                ranking.ranking,
                ranking.total_students,
                ranking.percentile
            ))
            conn.commit()
        
        return has_changed
    
    def save_major_rankings(self, major: str, students: list[MajorStudentRanking]) -> tuple[bool, list[MajorStudentRanking]]:
        """
        保存专业全员排名快照
        返回 (是否有变动, 上一次的快照列表)
        """
        import uuid
        snapshot_id = str(uuid.uuid4())
        
        # 获取上一个快照进行对比
        latest_snapshot = self.get_latest_major_rankings(major)
        
        # 保存新快照
        with sqlite3.connect(self.db_path) as conn:
            for s in students:
                conn.execute('''
                    INSERT INTO major_rankings (snapshot_id, rank, name, gpa, credits, major, is_me)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (snapshot_id, s.rank, s.name, s.gpa, s.credits, s.major, 1 if s.is_me else 0))
            conn.commit()
            
        # 对比逻辑
        if not latest_snapshot:
            return True, []
            
        if len(latest_snapshot) != len(students):
            return True, latest_snapshot
            
        has_changed = False
        for old, new in zip(latest_snapshot, students):
            if old.rank != new.rank or abs(old.gpa - new.gpa) > 0.0001 or abs(old.credits - new.credits) > 0.0001:
                has_changed = True
                break
                
        return has_changed, latest_snapshot

    def get_latest_major_rankings(self, major: str, semester: str = "") -> list[MajorStudentRanking]:
        """获取最新的全员排名快照，可按学期筛选"""
        with sqlite3.connect(self.db_path) as conn:
            # 先找最新的 snapshot_id
            if semester:
                cursor = conn.execute(
                    'SELECT snapshot_id FROM major_rankings WHERE major = ? AND semester = ? ORDER BY timestamp DESC LIMIT 1',
                    (major, semester)
                )
            else:
                # 当 semester 为空时，明确查找 semester 为空字符串或 NULL 的记录
                # 否则可能会获取到带学期的快照（如最新的本学期推断快照）
                cursor = conn.execute(
                    "SELECT snapshot_id FROM major_rankings WHERE major = ? AND (semester = '' OR semester IS NULL) ORDER BY timestamp DESC LIMIT 1",
                    (major,)
                )
            row = cursor.fetchone()
            if not row:
                return []
            
            snapshot_id = row[0]
            cursor = conn.execute(
                '''SELECT rank, name, gpa, credits, major, is_me, semester 
                   FROM major_rankings WHERE snapshot_id = ? ORDER BY rank ASC''',
                (snapshot_id,)
            )
            return [
                MajorStudentRanking(
                    rank=r[0], name=r[1], gpa=r[2], credits=r[3], major=r[4], 
                    is_me=bool(r[5]), semester=r[6] if r[6] else ""
                )
                for r in cursor.fetchall()
            ]

    def save_semester_rankings(self, major: str, semester: str, students: list[MajorStudentRanking], infer_pnp: bool = True) -> tuple[bool, list[MajorStudentRanking], list[GradeInference]]:
        """
        保存按学期的专业排名快照
        
        Args:
            major: 专业名称
            semester: 学期
            students: 学生排名列表
            infer_pnp: 是否启用 P/NP 课程自动识别
        
        返回 (是否有变动, 上一次快照, 推断的成绩列表)
        """
        import uuid
        snapshot_id = str(uuid.uuid4())
        
        # 获取上一个快照进行对比
        latest_snapshot = self.get_latest_major_rankings(major, semester)
        
        # 保存新快照
        with sqlite3.connect(self.db_path) as conn:
            for s in students:
                conn.execute('''
                    INSERT INTO major_rankings (snapshot_id, rank, name, gpa, credits, major, semester, is_me)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (snapshot_id, s.rank, s.name, s.gpa, s.credits, s.major, semester, 1 if s.is_me else 0))
            conn.commit()
        
        # 推断成绩
        inferences: list[GradeInference] = []
        
        if not latest_snapshot:
            return True, [], inferences
        
        # === 优化 1: 按专业分组 (Partition by Major) ===
        # 减少推断范围，避免跨专业错误匹配
        # 用户反馈: "同专业推断出的结果...减少全学院其他专业推断...一一对应"
        
        from collections import defaultdict
        
        # 分组 New Snapshot
        new_by_major = defaultdict(list)
        # 建立原始索引映射，以便后续定位
        new_indices_map = defaultdict(list) 
        for i, s in enumerate(students):
            new_by_major[s.major].append(s)
            new_indices_map[s.major].append(i)
            
        # 分组 Old Snapshot
        old_by_major = defaultdict(list)
        for s in latest_snapshot:
            old_by_major[s.major].append(s)
            
        has_changed = False
        
        # 对每个专业独立执行匹配算法
        for major_name, major_new_students in new_by_major.items():
            major_old_students = old_by_major.get(major_name, [])
            
            # 如果该专业在旧快照中不存在，说明全是新人，标记变更但不推断
            if not major_old_students:
                has_changed = True
                continue
                
            # 执行匹配 (Helper Logic)
            major_inferences, major_changed = self._match_students_in_major(
                major_old_students, major_new_students, semester, infer_pnp
            )
            
            if major_changed:
                has_changed = True
            if major_inferences:
                inferences.extend(major_inferences)
                
        # 检查是否有未处理的旧专业 (旧快照有，新快照没了) -> 这种算变更
        for old_major in old_by_major:
            if old_major not in new_by_major:
                has_changed = True
        
        # 保存推断结果
        if inferences:
            self.save_inferences(inferences)
        
        return has_changed, latest_snapshot, inferences

        return inferences, has_changed

    def _match_students_in_major(self, old_students: list[MajorStudentRanking], new_students: list[MajorStudentRanking], semester: str, infer_pnp: bool) -> tuple[list[GradeInference], bool]:
        """
        在单个专业内部执行全局优先匹配算法 (Global Priority Matching)
        解决顺序依赖问题：优先选择"质量最高"的匹配，而不是"先来后到"
        """
        inferences = []
        has_changed = False
        
        old_map = {i: s for i, s in enumerate(old_students)}
        used_old_indices = set()
        used_new_indices = set()
        
        # === 全局评分匹配 (Unified Global Scorer) ===
        # 将 "精确匹配" 和 "推断匹配" 统一在同一个池子中竞争
        # 引入 Rank Distance 作为 Tie-breaker，解决同分碰撞时的身份漂移问题
        
        candidates = [] # list of (score, new_idx, old_idx, credit_diff, priority_type)
        
        # 遍历所有组合
        for new_idx, new_s in enumerate(new_students):
            for old_idx, old_s in old_map.items():
                
                credit_diff = new_s.credits - old_s.credits
                gpa_diff = abs(new_s.gpa - old_s.gpa)
                
                # --- 统一评分 (Unified Scoring) ---
                # A. 精确匹配 (Exact)
                if abs(credit_diff) < 0.001 and gpa_diff < 0.001:
                    # 视为 Diff=0 的极佳匹配
                    # 给一个巨大的 Base 分数，确保 "无变化" 的人永远优先匹配自己，
                    # 即使因为别人成绩变化导致自己的 Rank 发生了巨大偏移 (Rank Penalty)。
                    # 10000 分足以抵抗 Rank Dist * 0.5 (即使 1000名差距也才扣 500分)
                    score = 10000 
                    
                    # Rank Penalty (Still applied as tie-breaker for identical exact matches)
                    rank_dist = abs(new_s.rank - old_s.rank)
                    score -= rank_dist * 0.5
                    
                    candidates.append({
                        'score': score,
                        'new_idx': new_idx,
                        'old_idx': old_idx,
                        'diff': 0,
                        'type': 'exact'
                    })
                    continue
                
                # B. 推断匹配 (Inference)
                if 0.5 <= credit_diff <= 15:
                    old_weighted = old_s.gpa * old_s.credits
                    new_weighted = new_s.gpa * new_s.credits
                    inferred_gpa = (new_weighted - old_weighted) / credit_diff
                    
                    is_valid = self._is_mathematically_possible(old_s, new_s, credit_diff, infer_pnp)
                    if not is_valid: continue
                    
                    score = 0
                    
                    # 1. 学分变动幅度
                    if credit_diff < 5.0: score += 1000
                    else: score += 0
                    
                    # 2. 整数偏好
                    is_integer = abs(credit_diff - round(credit_diff)) < 0.001
                    if is_integer: score += 500
                    
                    # 3. 变动幅度惩罚
                    score -= credit_diff
                    
                    # 4. Rank Penalty
                    rank_dist = abs(new_s.rank - old_s.rank)
                    score -= rank_dist * 0.5

                    candidates.append({
                        'score': score,
                        'new_idx': new_idx,
                        'old_idx': old_idx,
                        'diff': credit_diff,
                        'type': 'inference'
                    })
        
        # 按分数降序排序
        candidates.sort(key=lambda x: x['score'], reverse=True)
        
        # 贪心消耗
        for cand in candidates:
            n_idx = cand['new_idx']
            o_idx = cand['old_idx']
            
            if n_idx in used_new_indices or o_idx in used_old_indices:
                continue
                
            # Lock It
            used_new_indices.add(n_idx)
            used_old_indices.add(o_idx)
            
            if cand['type'] == 'inference':
                # 只有推断匹配才生成 Inference (精确匹配不算变化)
                has_changed = True
                matches_found_flag = True
                
                old_s = old_map[o_idx]
                new_s = new_students[n_idx]
                inference = GradeInference.from_change(old_s, new_s, semester, infer_pnp=infer_pnp)
                if inference:
                    inferences.append(inference)
            else:
                # Exact match - do nothing but mark used
                pass 
                
        # 检查是否有未匹配的新增条目 (视为变化)
        if matches_found_flag or len(used_new_indices) < len(new_students):
             has_changed = True
             
        return inferences, has_changed

    def _is_mathematically_possible(self, old_s, new_s, credit_diff, infer_pnp):
        """
        判断推断出的绩点是否在数学上可能落入合法的复旦绩点区间
        考虑到源数据的 GPA 是四舍五入到 2 位小数的
        """
        # 复旦合法绩点区间 (User Provided)
        # 补充了一些缓冲以防边界定义过死
        valid_intervals = [
            (3.95, 4.05), # A: 4.0
            (3.65, 3.85), # A-: 3.7-3.8
            (3.25, 3.65), # B+: 3.3-3.6
            (2.95, 3.25), # B: 3.0-3.2
            (2.65, 2.95), # B-: 2.7-2.9
            (2.25, 2.65), # C+: 2.3-2.6
            (1.95, 2.25), # C: 2.0-2.2
            (1.65, 1.95), # C-: 1.7-1.9
            (1.25, 1.35), # D: 1.3
            (0.95, 1.05), # D-: 1.0
            (-0.05, 0.05) # F: 0
        ]
        
        # 对于大额学分变动 (Composite)，放宽检查，只要在大范围内即可
        if credit_diff >= 4.5:
             # 只要在 -0.1 ~ 4.05 之间就算可能 (复合课程均值可能落在任何地方)
             # 但必须排除 > 4.05 的情况
             # 计算 min/max inferred 看看是否完全超标
             pass # 继续往下算 range
             
        # 计算 Old 和 New 的真实总分可能范围 (反向去四舍五入)
        # GPA 3.00 -> [2.995, 3.005)
        old_gpa_min = old_s.gpa - 0.005
        old_gpa_max = old_s.gpa + 0.005
        
        new_gpa_min = new_s.gpa - 0.005
        new_gpa_max = new_s.gpa + 0.005
        
        # 推断出的学分绩点 X 的范围:
        # X = (NewTotal - OldTotal) / Diff
        # Min X occurs when New is smallest and Old is largest
        inf_min = (new_gpa_min * new_s.credits - old_gpa_max * old_s.credits) / credit_diff
        # Max X occurs when New is largest and Old is smallest
        inf_max = (new_gpa_max * new_s.credits - old_gpa_min * old_s.credits) / credit_diff
        
        # 检查是否与任意合法区间有交集
        has_overlap = False
        
        # 如果是大额更新，我们只检查总体界限，因为多门课平均分可以是任意值(如3.49)，会填补Gap
        if credit_diff >= 4.5:
            # 大额更新允许落在 Gap 里 (如 3.49)，只要不超出 4.05 或低于 -0.1
             if inf_max >= -0.1 and inf_min <= 4.05:
                 return True
             return False
        
        # 小额更新，必须落在特定区间内
        for (v_min, v_max) in valid_intervals:
            # 检查区间 [inf_min, inf_max] 与 [v_min, v_max] 是否重叠
            if max(inf_min, v_min) < min(inf_max, v_max):
                has_overlap = True
                break
                
        # 特殊处理 PNP (如果开启)
        if not has_overlap and infer_pnp:
             # PNP 通常是 0 左右
             if max(inf_min, -0.1) < min(inf_max, 0.1):
                 has_overlap = True
                 
        return has_overlap
        
        # 保存推断结果
        if inferences:
            self.save_inferences(inferences)
        
        return has_changed, latest_snapshot, inferences

    def save_inferences(self, inferences: list[GradeInference]) -> None:
        """保存推断的成绩到数据库"""
        with sqlite3.connect(self.db_path) as conn:
            for inf in inferences:
                conn.execute('''
                    INSERT INTO grade_inferences 
                    (rank, name, old_gpa, new_gpa, old_credits, new_credits, inferred_gpa, inferred_credits, major, semester, is_pnp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    inf.rank, inf.name, inf.old_gpa, inf.new_gpa, 
                    inf.old_credits, inf.new_credits, inf.inferred_gpa, 
                    inf.inferred_credits, inf.major, inf.semester,
                    1 if inf.is_pnp else 0
                ))
            conn.commit()

    def get_latest_inferences(self, major: str = "", semester: str = "", limit: int = 50) -> list[GradeInference]:
        """获取最新的推断成绩记录"""
        with sqlite3.connect(self.db_path) as conn:
            query = 'SELECT rank, name, old_gpa, new_gpa, old_credits, new_credits, inferred_gpa, inferred_credits, major, semester, timestamp, is_pnp FROM grade_inferences'
            params = []
            conditions = []
            
            if major:
                conditions.append('major = ?')
                params.append(major)
            if semester:
                conditions.append('semester = ?')
                params.append(semester)
            
            if conditions:
                query += ' WHERE ' + ' AND '.join(conditions)
            
            query += ' ORDER BY timestamp DESC LIMIT ?'
            params.append(limit)
            
            cursor = conn.execute(query, params)
            return [
                GradeInference(
                    rank=r[0], name=r[1], old_gpa=r[2], new_gpa=r[3],
                    old_credits=r[4], new_credits=r[5], inferred_gpa=r[6],
                    inferred_credits=r[7], major=r[8], semester=r[9],
                    timestamp=datetime.fromisoformat(r[10]) if r[10] else None,
                    is_pnp=bool(r[11]) if len(r) > 11 else False
                )
                for r in cursor.fetchall()
            ]

    def get_ranking_history(self, major: str, limit: int = 20) -> list[GpaRanking]:
        """获取排名历史记录"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                '''SELECT major, gpa, ranking, total_students, percentile, timestamp 
                   FROM rankings WHERE major = ? ORDER BY timestamp DESC LIMIT ?''',
                (major, limit)
            )
            return [
                GpaRanking(
                    major=row[0], gpa=row[1], ranking=row[2],
                    total_students=row[3], percentile=row[4],
                    timestamp=datetime.fromisoformat(row[5]) if row[5] else None
                )
                for row in cursor.fetchall()
            ]

