"""
SQLite 数据库模块
用于存储和查询成绩历史记录
"""
import logging
import sqlite3
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


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
            # track_name 用于标识监控目标（如“软件工程”或“计算与智能创新学院”）
            # major 用于存储学生的实际专业名
            conn.execute('''
                CREATE TABLE IF NOT EXISTS major_rankings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    gpa REAL NOT NULL,
                    credits REAL NOT NULL,
                    major TEXT NOT NULL,
                    track_name TEXT,
                    semester TEXT DEFAULT '',
                    is_me INTEGER DEFAULT 0,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # --- 自动迁移: 为旧表添加 track_name 字段 ---
            cursor = conn.execute("PRAGMA table_info(major_rankings)")
            columns = [column[1] for column in cursor.fetchall()]
            if 'track_name' not in columns:
                logger.info("正在为 major_rankings 表添加 track_name 字段...")
                conn.execute("ALTER TABLE major_rankings ADD COLUMN track_name TEXT")
                # 初始数据迁移：将原有的 major 拷贝到 track_name
                conn.execute("UPDATE major_rankings SET track_name = major WHERE track_name IS NULL")
                conn.commit()
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
    
    def save_major_rankings(self, track_name: str, students: list[MajorStudentRanking]) -> tuple[bool, list[MajorStudentRanking]]:
        """
        保存专业全员排名快照
        track_name: 监控目标（如专业名或院系名）
        返回 (是否有变动, 上一次的快照列表)
        """
        import uuid
        snapshot_id = str(uuid.uuid4())
        
        # 获取上一个快照进行对比 (通过 track_name)
        latest_snapshot = self.get_latest_major_rankings(track_name)
        
        # 保存新快照
        with sqlite3.connect(self.db_path) as conn:
            for s in students:
                conn.execute('''
                    INSERT INTO major_rankings (snapshot_id, rank, name, gpa, credits, major, track_name, is_me)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (snapshot_id, s.rank, s.name, s.gpa, s.credits, s.major, track_name, 1 if s.is_me else 0))
            conn.commit()
            
        # 对比逻辑 (基于内容而非位置，避免同绩点顺序变化误判)
        if not latest_snapshot:
            return True, []
            
        if len(latest_snapshot) != len(students):
            return True, latest_snapshot
        
        # 使用 (gpa, credits) 元组集合进行内容比较
        # 忽略排名顺序变化，只检测实际数据变化
        def make_content_set(snapshot):
            """将快照转换为 (gpa, credits) 的 frozenset，用于内容比较"""
            # 使用四舍五入到4位小数避免浮点精度问题
            return frozenset(
                (round(s.gpa, 4), round(s.credits, 4)) 
                for s in snapshot
            )
        
        old_content = make_content_set(latest_snapshot)
        new_content = make_content_set(students)
        
        has_changed = old_content != new_content
                
        return has_changed, latest_snapshot

    def get_latest_major_rankings(self, track_name: str, semester: str = "") -> list[MajorStudentRanking]:
        """获取最新的全员排名快照，按 track_name (监控目标) 查找"""
        with sqlite3.connect(self.db_path) as conn:
            # 先找最新的 snapshot_id
            if semester:
                cursor = conn.execute(
                    'SELECT snapshot_id FROM major_rankings WHERE track_name = ? AND semester = ? ORDER BY timestamp DESC LIMIT 1',
                    (track_name, semester)
                )
            else:
                # 当 semester 为空时，明确查找 semester 为空字符串或 NULL 的记录
                cursor = conn.execute(
                    "SELECT snapshot_id FROM major_rankings WHERE track_name = ? AND (semester = '' OR semester IS NULL) ORDER BY timestamp DESC LIMIT 1",
                    (track_name,)
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

    def save_semester_rankings(self, track_name: str, semester: str, students: list[MajorStudentRanking], infer_pnp: bool = True) -> tuple[bool, list[MajorStudentRanking], list[GradeInference]]:
        """
        保存按学期的专业排名快照
        
        Args:
            track_name: 监控目标（专业或院系名）
            semester: 学期
            students: 学生排名列表
            infer_pnp: 是否启用 P/NP 课程自动识别
        
        返回 (是否有变动, 上一次快照, 推断的成绩列表)
        """
        import uuid
        snapshot_id = str(uuid.uuid4())
        
        # 获取上一个快照进行对比 (通过 track_name)
        latest_snapshot = self.get_latest_major_rankings(track_name, semester)
        
        # 保存新快照
        with sqlite3.connect(self.db_path) as conn:
            for s in students:
                conn.execute('''
                    INSERT INTO major_rankings (snapshot_id, rank, name, gpa, credits, major, track_name, semester, is_me)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (snapshot_id, s.rank, s.name, s.gpa, s.credits, s.major, track_name, semester, 1 if s.is_me else 0))
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
        在单个专业内部执行全局最优匹配算法 (Hungarian Algorithm)
        使用 scipy.optimize.linear_sum_assignment 实现二分图最优匹配
        确保全局代价最小，而不是贪心局部最优
        
        增强：两阶段同学分约束
        - 阶段1: 预扫描找到最可能的公共学分差（众数）
        - 阶段2: 对学分差等于众数的匹配给予代价减免
        """
        import logging
        from collections import Counter
        logger = logging.getLogger(__name__)
        
        inferences = []
        has_changed = False
        
        n_old = len(old_students)
        n_new = len(new_students)
        
        if n_old == 0 or n_new == 0:
            has_changed = n_new > 0
            return inferences, has_changed
        
        # === 阶段1: 预扫描找到众数学分差 ===
        pre_scan_diffs = []
        for old_idx, old_s in enumerate(old_students):
            for new_idx, new_s in enumerate(new_students):
                credit_diff = new_s.credits - old_s.credits
                gpa_diff = abs(new_s.gpa - old_s.gpa)
                
                if abs(credit_diff) < 0.001 and gpa_diff < 0.001:
                    continue
                    
                if 0.5 <= credit_diff <= 15:
                    is_valid = self._is_mathematically_possible(old_s, new_s, credit_diff, infer_pnp)
                    if is_valid:
                        pre_scan_diffs.append(round(credit_diff))
        
        target_credit_diff = None
        if pre_scan_diffs:
            diff_counter = Counter(pre_scan_diffs)
            most_common = diff_counter.most_common(1)
            if most_common and most_common[0][1] >= 2:
                target_credit_diff = most_common[0][0]
                logger.debug(f'同学分约束: 预扫描众数={target_credit_diff}学分 (出现{most_common[0][1]}次)')
        
        # === 阶段2: 构建代价矩阵 (加入同学分奖励) ===
        INF = 1e9
        cost_matrix = [[INF] * n_new for _ in range(n_old)]
        match_info = {}
        
        for old_idx, old_s in enumerate(old_students):
            for new_idx, new_s in enumerate(new_students):
                
                credit_diff = new_s.credits - old_s.credits
                gpa_diff = abs(new_s.gpa - old_s.gpa)
                rank_dist = abs(new_s.rank - old_s.rank)
                
                # A. 精确匹配 (Exact)
                if abs(credit_diff) < 0.001 and gpa_diff < 0.001:
                    cost = -10000 + rank_dist * 1.0
                    cost_matrix[old_idx][new_idx] = cost
                    match_info[(old_idx, new_idx)] = {'type': 'exact', 'diff': 0}
                    continue
                
                # B. 推断匹配 (Inference)
                if 0.5 <= credit_diff <= 15:
                    is_valid = self._is_mathematically_possible(old_s, new_s, credit_diff, infer_pnp)
                    if not is_valid:
                        continue
                    
                    cost = 100
                    
                    if credit_diff >= 5.0:
                        cost += 2000
                    elif credit_diff >= 4.0:
                        cost += 500
                    
                    is_integer = abs(credit_diff - round(credit_diff)) < 0.001
                    if not is_integer:
                        cost += 300
                    
                    cost += credit_diff * 10
                    cost += rank_dist * 0.1
                    
                    # === 新增: 同学分一致性奖励 ===
                    if target_credit_diff is not None:
                        if abs(round(credit_diff) - target_credit_diff) < 0.5:
                            cost -= 800  # 代价减免，鼓励选择与众数一致的匹配
                    
                    cost_matrix[old_idx][new_idx] = cost
                    match_info[(old_idx, new_idx)] = {'type': 'inference', 'diff': credit_diff}
        
        # === 运行匈牙利算法 ===
        try:
            from scipy.optimize import linear_sum_assignment
            row_ind, col_ind = linear_sum_assignment(cost_matrix)
        except ImportError:
            # 如果 scipy 不可用，回退到贪心算法
            return self._match_students_greedy_fallback(old_students, new_students, semester, infer_pnp)
        
        # === 处理匹配结果 ===
        used_new_indices = set()
        
        for old_idx, new_idx in zip(row_ind, col_ind):
            cost = cost_matrix[old_idx][new_idx]
            
            # 跳过无效匹配 (INF 代价)
            if cost >= INF - 1:
                continue
            
            used_new_indices.add(new_idx)
            
            key = (old_idx, new_idx)
            if key in match_info:
                info = match_info[key]
                
                if info['type'] == 'inference':
                    has_changed = True
                    old_s = old_students[old_idx]
                    new_s = new_students[new_idx]
                    inference = GradeInference.from_change(old_s, new_s, semester, infer_pnp=infer_pnp)
                    if inference:
                        inferences.append(inference)
                # Exact match - just mark as used, no inference
        
        # 检查未匹配的新记录 (新增学生)
        if len(used_new_indices) < n_new:
            has_changed = True
        
        # === 后处理: 尝试统一同时出分课程的学分 ===
        if len(inferences) >= 2:
            inferences = self._try_unify_credit_diffs(
                inferences, old_students, new_students,
                set(), used_new_indices,  # 匈牙利算法没用 used_old_indices，传空集
                semester, infer_pnp
            )
             
        return inferences, has_changed
    
    def _match_students_greedy_fallback(self, old_students: list[MajorStudentRanking], new_students: list[MajorStudentRanking], semester: str, infer_pnp: bool) -> tuple[list[GradeInference], bool]:
        """
        当 scipy 不可用时的贪心回退算法
        增强：两阶段同学分约束
        """
        import logging
        from collections import Counter
        logger = logging.getLogger(__name__)
        
        inferences = []
        has_changed = False
        matches_found_flag = False
        
        old_map = {i: s for i, s in enumerate(old_students)}
        used_old_indices = set()
        used_new_indices = set()
        
        # === 阶段1: 预扫描找到众数学分差 ===
        pre_scan_diffs = []
        for new_idx, new_s in enumerate(new_students):
            for old_idx, old_s in old_map.items():
                credit_diff = new_s.credits - old_s.credits
                gpa_diff = abs(new_s.gpa - old_s.gpa)
                
                if abs(credit_diff) < 0.001 and gpa_diff < 0.001:
                    continue
                if 0.5 <= credit_diff <= 15:
                    is_valid = self._is_mathematically_possible(old_s, new_s, credit_diff, infer_pnp)
                    if is_valid:
                        pre_scan_diffs.append(round(credit_diff))
        
        target_credit_diff = None
        if pre_scan_diffs:
            diff_counter = Counter(pre_scan_diffs)
            most_common = diff_counter.most_common(1)
            if most_common and most_common[0][1] >= 2:
                target_credit_diff = most_common[0][0]
                logger.debug(f'同学分约束(回退): 预扫描众数={target_credit_diff}学分')
        
        # === 阶段2: 构建候选 (加入同学分奖励) ===
        candidates = []
        
        for new_idx, new_s in enumerate(new_students):
            for old_idx, old_s in old_map.items():
                
                credit_diff = new_s.credits - old_s.credits
                gpa_diff = abs(new_s.gpa - old_s.gpa)
                
                # Exact Match
                if abs(credit_diff) < 0.001 and gpa_diff < 0.001:
                    score = 10000 
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
                
                # Inference
                if 0.5 <= credit_diff <= 15:
                    is_valid = self._is_mathematically_possible(old_s, new_s, credit_diff, infer_pnp)
                    if not is_valid: continue
                    
                    score = 0
                    if credit_diff < 5.0: score += 1000
                    
                    is_integer = abs(credit_diff - round(credit_diff)) < 0.001
                    if is_integer: score += 500
                    
                    score -= credit_diff
                    
                    rank_dist = abs(new_s.rank - old_s.rank)
                    score -= rank_dist * 0.5
                    
                    # === 新增: 同学分一致性奖励 ===
                    if target_credit_diff is not None:
                        if abs(round(credit_diff) - target_credit_diff) < 0.5:
                            score += 800

                    candidates.append({
                        'score': score,
                        'new_idx': new_idx,
                        'old_idx': old_idx,
                        'diff': credit_diff,
                        'type': 'inference'
                    })
        
        candidates.sort(key=lambda x: x['score'], reverse=True)
        
        for cand in candidates:
            n_idx = cand['new_idx']
            o_idx = cand['old_idx']
            
            if n_idx in used_new_indices or o_idx in used_old_indices:
                continue
                
            used_new_indices.add(n_idx)
            used_old_indices.add(o_idx)
            
            if cand['type'] == 'inference':
                has_changed = True
                old_s = old_map[o_idx]
                new_s = new_students[n_idx]
                inference = GradeInference.from_change(old_s, new_s, semester, infer_pnp=infer_pnp)
                if inference:
                    inferences.append(inference)
                
        if len(used_new_indices) < len(new_students):
             has_changed = True
        
        # === 后处理: 尝试统一同时出分课程的学分 ===
        if len(inferences) >= 2:
            inferences = self._try_unify_credit_diffs(
                inferences, old_students, new_students,
                used_old_indices, used_new_indices,
                semester, infer_pnp
            )
             
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
        
        # === 新增：检查是否与原绩点相近 ===
        # 如果推断绩点接近原累计绩点，说明新课程成绩恰好等于累计绩点
        # 这是完全合理的情况
        if not has_overlap:
            old_gpa = old_s.gpa
            if max(inf_min, old_gpa - 0.1) < min(inf_max, old_gpa + 0.1):
                has_overlap = True
                
        # 特殊处理 PNP (如果开启)
        if not has_overlap and infer_pnp:
             # PNP 通常是 0 左右
             if max(inf_min, -0.1) < min(inf_max, 0.1):
                 has_overlap = True
                 
        return has_overlap

    def _try_unify_credit_diffs(
        self, 
        inferences: list[GradeInference], 
        old_students: list[MajorStudentRanking], 
        new_students: list[MajorStudentRanking],
        used_old_indices: set,
        used_new_indices: set,
        semester: str,
        infer_pnp: bool
    ) -> list[GradeInference]:
        """
        后处理：尝试将同时出分的课程统一为相同学分
        
        逻辑：
        1. 收集所有推断的学分差
        2. 找到众数（出现最多的整数学分值）
        3. 对于学分不等于众数的推断，尝试找替代匹配
        """
        if len(inferences) < 2:
            return inferences
            
        # 1. 收集所有推断的学分差（取整数）
        credit_diffs = [inf.inferred_credits for inf in inferences]
        rounded_diffs = [round(d) for d in credit_diffs]
        
        # 2. 计算众数
        from collections import Counter
        diff_counter = Counter(rounded_diffs)
        most_common = diff_counter.most_common(1)
        if not most_common:
            return inferences
            
        target_credit, target_count = most_common[0]
        
        # 如果众数只出现一次或所有学分已一致，直接返回
        if target_count < 2 or target_count == len(inferences):
            return inferences
            
        logger.debug(f'同学分约束: 众数={target_credit}学分 (出现{target_count}次), 尝试统一...')
        
        # 3. 尝试调整不一致的推断
        old_map = {i: s for i, s in enumerate(old_students)}
        
        # 记录当前匹配
        current_matches = {}
        for inf in inferences:
            for n_idx, n_s in enumerate(new_students):
                if n_s.rank == inf.rank and n_s.name == inf.name:
                    for o_idx, o_s in old_map.items():
                        if abs(o_s.gpa - inf.old_gpa) < 0.001 and abs(o_s.credits - inf.old_credits) < 0.001:
                            current_matches[n_idx] = o_idx
                            break
                    break
        
        adjusted_inferences = []
        adjustments_made = 0
        
        for inf in inferences:
            rounded_credit = round(inf.inferred_credits)
            
            if rounded_credit == target_credit:
                adjusted_inferences.append(inf)
                continue
                
            # 找到对应的 new 学生
            target_new_idx = None
            for n_idx, n_s in enumerate(new_students):
                if n_s.rank == inf.rank and n_s.name == inf.name:
                    target_new_idx = n_idx
                    break
                    
            if target_new_idx is None:
                adjusted_inferences.append(inf)
                continue
                
            new_s = new_students[target_new_idx]
            best_alternative = None
            
            for o_idx, o_s in old_map.items():
                if o_idx in used_old_indices and o_idx != current_matches.get(target_new_idx):
                    continue
                    
                alt_credit_diff = new_s.credits - o_s.credits
                
                if abs(alt_credit_diff - target_credit) < 0.1:
                    if self._is_mathematically_possible(o_s, new_s, alt_credit_diff, infer_pnp):
                        best_alternative = (o_idx, o_s, alt_credit_diff)
                        break
            
            if best_alternative:
                o_idx, o_s, alt_credit_diff = best_alternative
                new_inf = GradeInference.from_change(o_s, new_s, semester, infer_pnp=infer_pnp)
                if new_inf:
                    adjusted_inferences.append(new_inf)
                    adjustments_made += 1
                    logger.debug(f'  调整: {inf.name} 学分 {inf.inferred_credits:.1f} -> {new_inf.inferred_credits:.1f}')
                else:
                    adjusted_inferences.append(inf)
            else:
                adjusted_inferences.append(inf)
        
        if adjustments_made > 0:
            logger.info(f'同学分约束: 调整了 {adjustments_made} 条推断，统一至 {target_credit} 学分')
            
        return adjusted_inferences

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

