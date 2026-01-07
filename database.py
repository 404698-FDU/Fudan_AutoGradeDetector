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

    def _match_students_in_major(self, old_students: list[MajorStudentRanking], new_students: list[MajorStudentRanking], semester: str, infer_pnp: bool) -> tuple[list[GradeInference], bool]:
        """
        在单个专业内部执行双轮匹配算法
        返回: (inferences, has_changed)
        """
        inferences = []
        has_changed = False
        
        # 建立旧记录索引映射
        old_map = {i: s for i, s in enumerate(old_students)}
        used_old_indices = set()
        unmatched_new_indices = []
        
        # === 第一轮：精确匹配 ===
        for new_idx, new_s in enumerate(new_students):
            matched = False
            for old_idx, old_s in old_map.items():
                if old_idx in used_old_indices:
                    continue
                
                if abs(old_s.credits - new_s.credits) < 0.001 and abs(old_s.gpa - new_s.gpa) < 0.001:
                    used_old_indices.add(old_idx)
                    matched = True
                    break
            
            if not matched:
                unmatched_new_indices.append(new_idx)
        
        # === 第二轮：推断匹配 ===
        for new_idx in unmatched_new_indices:
            new_s = new_students[new_idx]
            
            best_match_idx = -1
            best_credit_diff = float('inf')
            best_is_integer = False
            best_is_small_update = False # 记录是否为小额更新 (< 5.0 学分)
            
            for old_idx, old_s in old_map.items():
                if old_idx in used_old_indices:
                    continue
                
                credit_diff = new_s.credits - old_s.credits
                
                # 放宽上限到 15 以兼容极端情况，但在优先级中惩罚 >= 5.0
                if 0.5 <= credit_diff <= 15:
                    old_weighted = old_s.gpa * old_s.credits
                    new_weighted = new_s.gpa * new_s.credits
                    inferred_gpa = (new_weighted - old_weighted) / credit_diff
                    
                    is_reasonable = False
                    if 1.25 <= inferred_gpa <= 4.05:
                        is_reasonable = True
                    elif -0.1 <= inferred_gpa <= 0.1:
                         if infer_pnp: is_reasonable = True
                         elif abs(inferred_gpa) <= 0.05: is_reasonable = True

                    if is_reasonable:
                        # === 优先级策略 V2 ===
                        # 1. 学分变动量 < 5.0 (Priority High) vs >= 5.0 (Priority Low)
                        #    用户反馈: "除非只能如此匹配，否则...匹配 >= 5分...重新尝试"
                        # 2. 整数变动 (Priority High) vs 小数变动 (Priority Low)
                        # 3. 变动量越小越好
                        
                        is_integer = abs(credit_diff - round(credit_diff)) < 0.001
                        is_small_update = credit_diff < 5.0
                        
                        replace_best = False
                        
                        if best_match_idx == -1:
                            replace_best = True
                        else:
                            # 比较当前侯选 (Current) vs 最佳侯选 (Best)
                            
                            # 规则 1: 优先选 < 5.0 的更新
                            if is_small_update and not best_is_small_update:
                                replace_best = True
                            elif not is_small_update and best_is_small_update:
                                replace_best = False
                            else:
                                # 规则 1 平局，看规则 2: 优先选整数
                                if is_integer and not best_is_integer:
                                    replace_best = True
                                elif not is_integer and best_is_integer:
                                    replace_best = False
                                else:
                                    # 规则 2 平局，看规则 3: 选更小的变动
                                    if credit_diff < best_credit_diff:
                                        replace_best = True
                        
                        if replace_best:
                            best_credit_diff = credit_diff
                            best_match_idx = old_idx
                            best_is_integer = is_integer
                            best_is_small_update = is_small_update
            
            if best_match_idx != -1:
                has_changed = True
                used_old_indices.add(best_match_idx)
                old_s = old_map[best_match_idx]
                inference = GradeInference.from_change(old_s, new_s, semester, infer_pnp=infer_pnp)
                if inference:
                    inferences.append(inference)
            else:
                has_changed = True
                
        return inferences, has_changed
        
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

