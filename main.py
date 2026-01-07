#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
复旦大学成绩自动检测器
主入口文件

使用方法：
    python main.py              # 单次检测
    python main.py --loop       # 持续监控模式
    python main.py --test-email # 测试邮件配置
    python main.py --show       # 显示已保存的成绩
"""
import argparse
import logging
import sys
import time
import io
from datetime import datetime

import schedule

from config import Config
from database import GradeDatabase, RankingDatabase
from email_notifier import EmailNotifier
from grade_detector import FudanGradeDetector

# 修复 Windows 终端 UTF-8 输出问题
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def setup_logging(verbose: bool = False):
    """配置日志"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('grade_detector.log', encoding='utf-8')
        ]
    )


def create_detector() -> FudanGradeDetector:
    """创建成绩检测器实例"""
    db = GradeDatabase(Config.DB_PATH)
    ranking_db = RankingDatabase(Config.DB_PATH)
    notifier = EmailNotifier(
        smtp_server=Config.SMTP_SERVER,
        smtp_port=Config.SMTP_PORT,
        sender=Config.EMAIL_SENDER,
        password=Config.EMAIL_PASSWORD,
        receiver=Config.EMAIL_RECEIVER,
        use_ssl=Config.SMTP_SSL
    )
    detector = FudanGradeDetector(
        username=Config.FUDAN_USERNAME,
        password=Config.FUDAN_PASSWORD,
        db=db,
        ranking_db=ranking_db,
        notifier=notifier,
        headless=Config.HEADLESS
    )
    return detector


def run_once():
    """执行单次检测"""
    logger = logging.getLogger(__name__)
    logger.info('=' * 50)
    logger.info('开始单次成绩及排名检测')
    
    detector = create_detector()
    results = detector.run_once()
    
    new_grades = results.get('new_grades', [])
    ranking_changed = results.get('ranking_changed', False)
    major_ranking_changed = results.get('major_ranking_changed', False)
    inferences = results.get('semester_inferences', [])
    dept_ranking_changed = results.get('department_ranking_changed', False)
    dept_inferences = results.get('department_inferences', [])
    
    # ===== 个人成绩检测 =====
    print('\n' + '=' * 50)
    print('📚 个人成绩检测')
    print('=' * 50)
    if new_grades:
        print(f'[OK] 发现 {len(new_grades)} 门新成绩：')
        for g in new_grades:
            print(f'   * {g.course_name}: {g.score}')
    else:
        print('[OK] 没有新成绩')
        
    if ranking_changed:
        print('[OK] 发现个人排名变化，已发送邮件通知')
    else:
        print('[OK] 个人排名无变化')
    
    # ===== 功能1: 专业总排名监控 =====
    print('\n' + '=' * 50)
    print('📊 功能1: 专业总绩点排名监控')
    print('=' * 50)
    if major_ranking_changed:
        print('[OK] ❗ 发现全专业排名变动，已发送汇总邮件')
    else:
        print('[OK] 全专业总排名稳定')
    
    # ===== 功能2: 本学期成绩推断 =====
    print('\n' + '=' * 50)
    print('🔍 功能2: 本学期成绩推断')
    print('=' * 50)
    if inferences:
        print(f'[OK] 推断出 {len(inferences)} 个人的学分发生变化：')
        for inf in inferences:
            if getattr(inf, 'is_pnp', False):
                gpa_str = 'P/NP课程'
            elif inf.inferred_gpa is not None:
                gpa_str = f'{inf.inferred_gpa:.2f}'
            else:
                gpa_str = '无法计算'
            print(f'   * 排名 {inf.rank} ({inf.name}): +{inf.inferred_credits:.1f} 学分, 推断绩点 {gpa_str}')
        print('[OK] 已发送成绩推断通知邮件')
    else:
        print('[OK] 本学期暂无新成绩推断')
    
    # ===== 功能3: 院系排名监控 =====
    if Config.ENABLE_DEPARTMENT_MONITORING:
        print('\n' + '=' * 50)
        print(f'🏫 功能3: {Config.DEPARTMENT_NAME}全院排名监控')
        print('=' * 50)
        if dept_ranking_changed:
            print('[OK] ❗ 发现院系排名变动，已发送汇总邮件')
        else:
            print('[OK] 院系排名稳定')
            
        if dept_inferences:
            print(f'[OK] 推断出 {len(dept_inferences)} 个人的院系内学分变化：')
            for inf in dept_inferences:
                print(f'   * 排名 {inf.rank} ({inf.name}): +{inf.inferred_credits:.1f} 学分')
            print('[OK] 已发送院系成绩推断通知邮件')
    
    print('\n' + '=' * 50)
    logger.info('单次检测完成')


def run_loop():
    """持续监控模式"""
    logger = logging.getLogger(__name__)
    interval = Config.CHECK_INTERVAL_MINUTES
    
    print(f'''
========================================================
         复旦大学成绩自动检测器 - 持续监控模式
--------------------------------------------------------
  检测间隔: {interval:>3} 分钟
  按 Ctrl+C 停止
========================================================
''')
    
    def job():
        logger.info(f'定时任务触发 - {datetime.now()}')
        try:
            run_once()
        except Exception as e:
            logger.error(f'检测出错: {e}')
    
    # 立即执行一次
    job()
    
    # 设置定时任务
    schedule.every(interval).minutes.do(job)
    
    try:
        while True:
            schedule.run_pending()
            time.sleep(60)  # 每分钟检查一次调度
    except KeyboardInterrupt:
        print('\n\n[INFO] 已停止监控')
        logger.info('用户中断，停止监控')


def test_email():
    """测试邮件配置"""
    logger = logging.getLogger(__name__)
    print('[EMAIL] 正在发送测试邮件...')
    
    notifier = EmailNotifier(
        smtp_server=Config.SMTP_SERVER,
        smtp_port=Config.SMTP_PORT,
        sender=Config.EMAIL_SENDER,
        password=Config.EMAIL_PASSWORD,
        receiver=Config.EMAIL_RECEIVER,
        use_ssl=Config.SMTP_SSL
    )
    
    if notifier.send_test_email():
        print(f'[OK] 测试邮件发送成功！请检查 {Config.EMAIL_RECEIVER}')
    else:
        print('[ERROR] 测试邮件发送失败，请检查配置')
        sys.exit(1)


def show_grades():
    """显示已保存的成绩"""
    db = GradeDatabase(Config.DB_PATH)
    grades = db.get_all_grades()
    
    if not grades:
        print('[INFO] 暂无已保存的成绩记录')
        return
    
    print(f'\n[GRADES] 已保存的成绩记录 (共 {len(grades)} 条):\n')
    print(f'{"课程名称":<30} {"成绩":>8} {"绩点":>8} {"发现时间":<20}')
    print('-' * 70)
    
    for g in grades:
        gp = f'{g.grade_point:.3f}' if g.grade_point is not None else '-'
        first_seen = g.first_seen.strftime('%Y-%m-%d %H:%M') if g.first_seen else '-'
        print(f'{g.course_name:<30} {g.score:>8} {gp:>8} {first_seen:<20}')


def show_rankings():
    """显示绩点排名历史"""
    db = RankingDatabase(Config.DB_PATH)
    # 假设主要关注软件工程
    history = db.get_ranking_history("软件工程")
    
    if not history:
        print('[INFO] 暂无排名记录')
        return
        
    print(f'\n[RANKING] 绩点排名历史 (共 {len(history)} 条):\n')
    print(f'{"时间":<20} {"专业":<15} {"绩点":>8} {"排名":>10} {"百分比":>10}')
    print('-' * 70)
    
    for r in history:
        ts = r.timestamp.strftime('%Y-%m-%d %H:%M') if r.timestamp else '-'
        print(f'{ts:<20} {r.major:<15} {r.gpa:>8.3f} {r.ranking:>4}/{r.total_students:<5} {r.percentile*100:>9.2f}%')


def show_major_rankings():
    """显示全专业排名快照"""
    db = RankingDatabase(Config.DB_PATH)
    history = db.get_latest_major_rankings("软件工程")
    
    if not history:
        print('[INFO] 暂无全专业排名记录')
        return
        
    print(f'\n[MAJOR RANKING] 软件工程专业排名快照 (前 50 名):\n')
    print(f'{"排名":<4} {"姓名":<10} {"绩点":>8} {"学分":>8} {"备注"}')
    print('-' * 50)
    
    for r in history[:50]:
        me_tag = " (我)" if r.is_me else ""
        print(f'{r.rank:<4} {r.name:<10} {r.gpa:>8.3f} {r.credits:>8.1f} {me_tag}')


def show_department_rankings():
    """显示全院系排名快照"""
    db = RankingDatabase(Config.DB_PATH)
    history = db.get_latest_major_rankings(Config.DEPARTMENT_NAME)
    
    if not history:
        print(f'[INFO] 暂无{Config.DEPARTMENT_NAME}排名记录')
        return
        
    print(f'\n[DEPT RANKING] {Config.DEPARTMENT_NAME}排名快照 (前 50 名):\n')
    print(f'{"排名":<4} {"姓名":<10} {"绩点":>8} {"学分":>8} {"备注"}')
    print('-' * 50)
    
    for r in history[:50]:
        me_tag = " (我)" if r.is_me else ""
        print(f'{r.rank:<4} {r.name:<10} {r.gpa:>8.3f} {r.credits:>8.1f} {me_tag}')


def show_inferred_grades():
    """显示推断的成绩历史"""
    db = RankingDatabase(Config.DB_PATH)
    inferences = db.get_latest_inferences(limit=50)
    
    if not inferences:
        print('[INFO] 暂无推断成绩记录')
        return
    
    print(f'\n[INFERRED] 推断成绩历史 (最近 {len(inferences)} 条):\n')
    print(f'{"时间":<20} {"排名":<5} {"学生":<10} {"学分变化":<12} {"推断绩点":<10} {"类型":<8}')
    print('-' * 80)
    
    for inf in inferences:
        ts = inf.timestamp.strftime('%Y-%m-%d %H:%M') if inf.timestamp else '-'
        if getattr(inf, 'is_pnp', False):
            gpa_str = 'P/NP'
            type_str = '✅ P/NP'
        elif inf.inferred_gpa is not None:
            gpa_str = f'{inf.inferred_gpa:.2f}'
            type_str = '成绩'
        else:
            gpa_str = '-'
            type_str = '未知'
        credit_change = f'{inf.old_credits:.1f}→{inf.new_credits:.1f}'
        print(f'{ts:<20} {inf.rank:<5} {inf.name:<10} {credit_change:<12} {gpa_str:<10} {type_str:<8}')


def main():
    parser = argparse.ArgumentParser(
        description='复旦大学成绩自动检测器',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
    python main.py                # 单次检测（含成绩推断）
    python main.py --loop         # 持续监控模式
    python main.py --test-email   # 测试邮件配置
    python main.py --show         # 显示已保存的成绩
    python main.py --show-inferred # 显示推断的成绩历史
        '''
    )
    parser.add_argument('--loop', action='store_true', help='持续监控模式')
    parser.add_argument('--test-email', action='store_true', help='测试邮件配置')
    parser.add_argument('--show', action='store_true', help='显示已保存的成绩')
    parser.add_argument('--show-ranking', action='store_true', help='显示个人排名历史')
    parser.add_argument('--show-major', action='store_true', help='显示全专业排名快照')
    parser.add_argument('--show-department', action='store_true', help='显示全院系排名快照')
    parser.add_argument('--show-inferred', action='store_true', help='显示推断的成绩历史')
    parser.add_argument('--verbose', '-v', action='store_true', help='详细日志')
    parser.add_argument('--no-headless', action='store_true', help='显示浏览器窗口（调试用）')
    
    args = parser.parse_args()
    
    # 配置日志
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)
    
    # 如果指定了显示浏览器，覆盖配置
    if args.no_headless:
        Config.HEADLESS = False
    
    # 验证配置
    missing = Config.validate()
    if missing and not args.show:
        print('[ERROR] 配置不完整，缺少以下配置项：')
        for item in missing:
            print(f'   - {item}')
        print('\n请复制 .env.example 为 .env 并填写配置')
        sys.exit(1)
    
    # 执行对应操作
    if args.test_email:
        test_email()
    elif args.show:
        show_grades()
    elif args.show_ranking:
        show_rankings()
    elif args.show_major:
        show_major_rankings()
    elif args.show_department:
        show_department_rankings()
    elif args.show_inferred:
        show_inferred_grades()
    elif args.loop:
        run_loop()
    else:
        run_once()


if __name__ == '__main__':
    main()
