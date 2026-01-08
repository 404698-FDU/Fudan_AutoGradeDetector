"""
邮件通知模块
支持多种 SMTP 服务器发送成绩通知
"""
import smtplib
import logging
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from database import Grade, GpaRanking, MajorStudentRanking, GradeInference

from typing import Optional

logger = logging.getLogger(__name__)

# 重试配置
MAX_EMAIL_RETRIES = 3
RETRY_BASE_DELAY = 5  # 秒


class EmailNotifier:
    """邮件通知类"""
    
    def __init__(
        self,
        smtp_server: str,
        smtp_port: int,
        sender: str,
        password: str,
        receiver: str,
        use_ssl: bool = True
    ):
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.sender = sender
        self.password = password
        self.receiver = receiver
        self.use_ssl = use_ssl
        self._pending_notifications: list[dict] = []  # 待发送队列
    
    def _send_email_with_retry(self, msg: MIMEMultipart, max_retries: int = MAX_EMAIL_RETRIES) -> bool:
        """
        带重试机制的邮件发送
        使用指数退避策略
        """
        last_error = None
        
        for attempt in range(max_retries):
            try:
                if self.use_ssl:
                    with smtplib.SMTP_SSL(self.smtp_server, self.smtp_port, timeout=30) as server:
                        server.login(self.sender, self.password)
                        server.sendmail(self.sender, self.receiver, msg.as_string())
                else:
                    with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=30) as server:
                        server.starttls()
                        server.login(self.sender, self.password)
                        server.sendmail(self.sender, self.receiver, msg.as_string())
                
                logger.info(f'邮件发送成功 (尝试 {attempt + 1}/{max_retries})')
                return True
                
            except Exception as e:
                last_error = e
                delay = RETRY_BASE_DELAY * (2 ** attempt)  # 指数退避: 5s, 10s, 20s
                logger.warning(f'邮件发送失败 (尝试 {attempt + 1}/{max_retries}): {e}')
                
                if attempt < max_retries - 1:
                    logger.info(f'将在 {delay} 秒后重试...')
                    time.sleep(delay)
        
        logger.error(f'邮件发送最终失败，已重试 {max_retries} 次: {last_error}')
        return False
    
    def _create_grade_html(self, grades: list['Grade']) -> str:
        """生成成绩通知的 HTML 内容"""
        rows = ''
        for g in grades:
            gp_display = f'{g.grade_point:.3f}' if g.grade_point is not None else '-'
            rows += f'''
            <tr>
                <td style="padding: 12px; border-bottom: 1px solid #eee;">{g.course_name}</td>
                <td style="padding: 12px; border-bottom: 1px solid #eee; text-align: center; font-weight: bold; color: #2563eb;">{g.score}</td>
                <td style="padding: 12px; border-bottom: 1px solid #eee; text-align: center;">{gp_display}</td>
            </tr>
            '''
        
        html = f'''
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; padding: 20px;">
            <div style="max-width: 600px; margin: 0 auto; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 24px; text-align: center;">
                    <h1 style="margin: 0; font-size: 24px;">🎉 新成绩通知</h1>
                    <p style="margin: 8px 0 0; opacity: 0.9;">复旦大学教务系统</p>
                </div>
                
                <div style="padding: 24px;">
                    <p style="color: #666; margin-bottom: 20px;">
                        检测到 <strong>{len(grades)}</strong> 门新课程成绩已发布：
                    </p>
                    
                    <table style="width: 100%; border-collapse: collapse;">
                        <thead>
                            <tr style="background: #f8f9fa;">
                                <th style="padding: 12px; text-align: left; border-bottom: 2px solid #dee2e6;">课程名称</th>
                                <th style="padding: 12px; text-align: center; border-bottom: 2px solid #dee2e6;">成绩</th>
                                <th style="padding: 12px; text-align: center; border-bottom: 2px solid #dee2e6;">绩点</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows}
                        </tbody>
                    </table>
                    
                    <p style="color: #999; font-size: 12px; margin-top: 24px; text-align: center;">
                        检测时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                    </p>
                </div>
            </div>
        </body>
        </html>
        '''
        return html
    
    def send_notification(self, grades: list['Grade']) -> bool:
        """
        发送成绩通知邮件
        返回 True 表示发送成功
        """
        if not grades:
            logger.info('没有新成绩需要通知')
            return True
        
        try:
            # 创建邮件
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f'🎓 复旦成绩通知 - {len(grades)} 门新成绩'
            msg['From'] = self.sender
            msg['To'] = self.receiver
            
            # 纯文本备用内容
            text_content = '新成绩通知\n\n'
            for g in grades:
                text_content += f'- {g.course_name}: {g.score}\n'
            
            # HTML 内容
            html_content = self._create_grade_html(grades)
            
            msg.attach(MIMEText(text_content, 'plain', 'utf-8'))
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))
            
            # 发送邮件（带重试）
            if self._send_email_with_retry(msg):
                logger.info(f'成功发送 {len(grades)} 条成绩通知到 {self.receiver}')
                return True
            else:
                return False
        except Exception as e:
            logger.error(f'发送邮件失败: {e}')
            return False
    
    def _create_ranking_html(self, old: Optional['GpaRanking'], new: 'GpaRanking') -> str:
        """生成排名变化的 HTML 内容"""
        change_text = ""
        if old:
            if new.ranking < old.ranking:
                change_text = f'''<p style="color: #059669; font-weight: bold;">📈 排名上升了！从第 {old.ranking} 名提升至第 {new.ranking} 名。</p>'''
            elif new.ranking > old.ranking:
                change_text = f'''<p style="color: #dc2626; font-weight: bold;">📉 排名下降了。从第 {old.ranking} 名降至第 {new.ranking} 名。</p>'''
            else:
                change_text = f'''<p style="color: #666;">排名未发生变化，当前仍为第 {new.ranking} 名。</p>'''
        else:
            change_text = f'''<p style="color: #2563eb; font-weight: bold;">🆕 首次记录排名：第 {new.ranking} 名。</p>'''

        html = f'''
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"></head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; padding: 20px;">
            <div style="max-width: 600px; margin: 0 auto; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                <div style="background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%); color: white; padding: 24px; text-align: center;">
                    <h1 style="margin: 0; font-size: 24px;">📊 绩点排名更新</h1>
                    <p style="margin: 8px 0 0; opacity: 0.9;">{new.major}</p>
                </div>
                <div style="padding: 24px;">
                    {change_text}
                    <div style="margin-top: 20px; padding: 16px; background: #f8f9fa; border-radius: 8px;">
                        <table style="width: 100%; border-collapse: collapse;">
                            <tr>
                                <td style="padding: 8px 0; color: #666;">当前绩点：</td>
                                <td style="padding: 8px 0; text-align: right; font-weight: bold;">{new.gpa:.3f}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px 0; color: #666;">专业排名：</td>
                                <td style="padding: 8px 0; text-align: right; font-weight: bold;">{new.ranking} / {new.total_students}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px 0; color: #666;">排名占比：</td>
                                <td style="padding: 8px 0; text-align: right; font-weight: bold;">{new.percentile*100:.2f}%</td>
                            </tr>
                        </table>
                    </div>
                    <p style="color: #999; font-size: 12px; margin-top: 24px; text-align: center;">
                        检测时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                    </p>
                </div>
            </div>
        </body>
        </html>
        '''
        return html

    def send_ranking_notification(self, old: Optional['GpaRanking'], new: 'GpaRanking') -> bool:
        """发送排名变化通知邮件"""
        try:
            msg = MIMEMultipart('alternative')
            subject = f'📊 绩点排名变动 - {new.major}'
            if old:
                if new.ranking < old.ranking:
                    subject = f'📈 排名上升！{new.major} 第 {new.ranking} 名'
                elif new.ranking > old.ranking:
                    subject = f'📉 排名下降 - {new.major} 第 {new.ranking} 名'
            
            msg['Subject'] = subject
            msg['From'] = self.sender
            msg['To'] = self.receiver
            
            text_content = f'绩点排名更新\n专业: {new.major}\n绩点: {new.gpa:.3f}\n排名: {new.ranking}/{new.total_students}'
            html_content = self._create_ranking_html(old, new)
            
            msg.attach(MIMEText(text_content, 'plain', 'utf-8'))
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))
            
            # 发送邮件（带重试）
            if self._send_email_with_retry(msg):
                logger.info(f'成功发送排名通知到 {self.receiver}')
                return True
            else:
                return False
        except Exception as e:
            logger.error(f'发送排名通知失败: {e}')
            return False

    def send_test_email(self) -> bool:
        """发送测试邮件"""
        try:
            msg = MIMEText('这是一封测试邮件，说明邮件配置正确！', 'plain', 'utf-8')
            msg['Subject'] = '✅ 复旦成绩检测器 - 邮件配置测试成功'
            msg['From'] = self.sender
            msg['To'] = self.receiver
            
            if self.use_ssl:
                with smtplib.SMTP_SSL(self.smtp_server, self.smtp_port) as server:
                    server.login(self.sender, self.password)
                    server.sendmail(self.sender, self.receiver, msg.as_string())
            else:
                with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                    server.starttls()
                    server.login(self.sender, self.password)
                    server.sendmail(self.sender, self.receiver, msg.as_string())
            
            logger.info(f'测试邮件发送成功: {self.receiver}')
            return True
            
        except Exception as e:
            logger.error(f'测试邮件发送失败: {e}')
            return False

    def _create_major_ranking_html(self, major: str, current: list['MajorStudentRanking'], previous: list['MajorStudentRanking'], scope_label: str = "专业") -> str:
        """生成全员排名的 HTML 内容，包含变动高亮"""
        # 将上一次排名转为字典方便查询
        prev_map = {p.rank: p for p in previous}
        # 如果 rank 变了，也可以尝试通过 index 比较，但由于名字脱敏，rank 是唯一的“锚点”
        # 我们这里假设 rank 是稳定的索引，对比同一 rank 位置的 GPA/Credits 是否变化
        # 或者如果有 is_me，可以追踪个人的变化
        
        rows = ''
        for i, s in enumerate(current):
            # 基础样式
            row_style = 'background-color: #f0fdf4;' if s.is_me else ''
            
            # 查找上一次同一位置的数据进行对比
            prev_s = previous[i] if i < len(previous) else None
            
            gpa_change = ""
            credit_change = ""
            if prev_s:
                if s.gpa > prev_s.gpa:
                    gpa_change = '<span style="color: #059669; font-size: 11px; margin-left: 4px;">↑</span>'
                elif s.gpa < prev_s.gpa:
                    gpa_change = '<span style="color: #dc2626; font-size: 11px; margin-left: 4px;">↓</span>'
                
                if s.credits > prev_s.credits:
                    credit_change = '<span style="color: #059669; font-size: 11px; margin-left: 4px;">↑</span>'
            
            rows += f'''
            <tr style="{row_style}">
                <td style="padding: 8px 10px; border-bottom: 1px solid #eee; text-align: center;">{s.rank}</td>
                <td style="padding: 8px 10px; border-bottom: 1px solid #eee; text-align: center;">{s.name}</td>
                <td style="padding: 8px 10px; border-bottom: 1px solid #eee; text-align: center; font-weight: bold;">
                    {s.gpa:.3f}{gpa_change}
                </td>
                <td style="padding: 8px 10px; border-bottom: 1px solid #eee; text-align: center;">
                    {s.credits:.1f}{credit_change}
                </td>
            </tr>
            '''
        
        html = f'''
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"></head>
        <body style="font-family: sans-serif; background: #f9fafb; padding: 20px;">
            <div style="max-width: 600px; margin: 0 auto; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);">
                <div style="background: #10b981; color: white; padding: 20px; text-align: center;">
                    <h1 style="margin: 0; font-size: 20px;">📊 全{scope_label}排名完整变动</h1>
                    <p style="margin: 5px 0 0; opacity: 0.9;">{scope_label}：{major} (共 {len(current)} 人)</p>
                </div>
                <div style="padding: 20px;">
                    <p style="color: #4b5563; font-size: 14px; margin-bottom: 15px;">检测到榜单更新，以下是完整排名数据 (+ 表示增加，↑↓ 表示绩点变动)：</p>
                    <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
                        <thead>
                            <tr style="background: #f3f4f6;">
                                <th style="padding: 10px; border-bottom: 2px solid #e5e7eb;">排名</th>
                                <th style="padding: 10px; border-bottom: 2px solid #e5e7eb;">学生</th>
                                <th style="padding: 10px; border-bottom: 2px solid #e5e7eb;">绩点</th>
                                <th style="padding: 10px; border-bottom: 2px solid #e5e7eb;">学分</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows}
                        </tbody>
                    </table>
                    <p style="color: #9ca3af; font-size: 12px; margin-top: 20px; text-align: center;">
                        检测时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                    </p>
                </div>
            </div>
        </body>
        </html>
        '''
        return html

    def send_major_ranking_update(self, major: str, current: list['MajorStudentRanking'], previous: list['MajorStudentRanking'], scope_label: str = "专业") -> bool:
        """发送全员排名变动通知 (完整版)"""
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f'📢 [{scope_label}] 排名变动通知 - {major} (完整榜单)'
            msg['From'] = self.sender
            msg['To'] = self.receiver
            
            text_content = f' [Main] 全{scope_label}排名完整变动通知\n{scope_label}: {major}\n总人数: {len(current)}'
            html_content = self._create_major_ranking_html(major, current, previous, scope_label)
            
            msg.attach(MIMEText(text_content, 'plain', 'utf-8'))
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))
            
            # 发送邮件（带重试）
            if self._send_email_with_retry(msg):
                logger.info(f'成功发送全专业排名变动通知 (完整版)')
                return True
            else:
                return False
        except Exception as e:
            logger.error(f'发送全专业排名通知失败: {e}')
            return False

    def _create_grade_inference_html(self, major: str, semester: str, inferences: list['GradeInference'], scope_label: str = "专业") -> str:
        """生成成绩推断通知的 HTML 内容"""
        rows = ''
        for inf in inferences:
            # P/NP 课程特殊显示
            if getattr(inf, 'is_pnp', False):
                gpa_display = '<span style="background: #fef3c7; padding: 2px 6px; border-radius: 4px;">P/NP</span>'
                gpa_color = '#92400e'  # 棕色
            elif inf.inferred_gpa is not None:
                gpa_display = f'{inf.inferred_gpa:.2f}'
                # 根据推断绩点设置颜色
                if inf.inferred_gpa >= 3.7:
                    gpa_color = '#059669'  # 绿色 - 优秀
                elif inf.inferred_gpa >= 3.0:
                    gpa_color = '#2563eb'  # 蓝色 - 良好
                elif inf.inferred_gpa >= 2.0:
                    gpa_color = '#d97706'  # 橙色 - 中等
                else:
                    gpa_color = '#dc2626'  # 红色 - 较低
            else:
                gpa_display = '无法计算'
                gpa_color = '#6b7280'  # 灰色
            
            # P/NP 课程行高亮
            row_style = 'background-color: #fffbeb;' if getattr(inf, 'is_pnp', False) else ''
            
            rows += f'''
            <tr style="{row_style}">
                <td style="padding: 10px; border-bottom: 1px solid #e5e7eb; text-align: center;">{inf.rank}</td>
                <td style="padding: 10px; border-bottom: 1px solid #e5e7eb; text-align: center;">{inf.name}</td>
                <td style="padding: 10px; border-bottom: 1px solid #e5e7eb; text-align: center;">{inf.old_gpa:.3f} → {inf.new_gpa:.3f}</td>
                <td style="padding: 10px; border-bottom: 1px solid #e5e7eb; text-align: center;">{inf.old_credits:.1f} → {inf.new_credits:.1f}</td>
                <td style="padding: 10px; border-bottom: 1px solid #e5e7eb; text-align: center; color: #059669; font-weight: bold;">+{inf.inferred_credits:.1f}</td>
                <td style="padding: 10px; border-bottom: 1px solid #e5e7eb; text-align: center; color: {gpa_color}; font-weight: bold;">{gpa_display}</td>
            </tr>
            '''
        
        html = f'''
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"></head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f9fafb; padding: 20px;">
            <div style="max-width: 700px; margin: 0 auto; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);">
                <div style="background: linear-gradient(135deg, #8b5cf6 0%, #6366f1 100%); color: white; padding: 24px; text-align: center;">
                    <h1 style="margin: 0; font-size: 22px;">🔍 成绩推断通知</h1>
                    <p style="margin: 8px 0 0; opacity: 0.9;">{major} · {semester}</p>
                </div>
                <div style="padding: 24px;">
                    <div style="background: #fef3c7; border-left: 4px solid #f59e0b; padding: 12px 16px; margin-bottom: 20px; border-radius: 4px;">
                        <p style="margin: 0; color: #92400e; font-size: 14px;">
                            ⚠️ 以下成绩为根据 GPA 和学分变化<strong>推断</strong>得出，仅供参考，可能存在误差。
                        </p>
                    </div>
                    
                    <p style="color: #4b5563; margin-bottom: 16px;">
                        检测到 <strong style="color: #6366f1;">{len(inferences)}</strong> 位同学的学分发生变化：
                    </p>
                    
                    <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
                        <thead>
                            <tr style="background: #f3f4f6;">
                                <th style="padding: 12px 8px; border-bottom: 2px solid #e5e7eb;">排名</th>
                                <th style="padding: 12px 8px; border-bottom: 2px solid #e5e7eb;">学生</th>
                                <th style="padding: 12px 8px; border-bottom: 2px solid #e5e7eb;">GPA 变化</th>
                                <th style="padding: 12px 8px; border-bottom: 2px solid #e5e7eb;">学分变化</th>
                                <th style="padding: 12px 8px; border-bottom: 2px solid #e5e7eb;">新增学分</th>
                                <th style="padding: 12px 8px; border-bottom: 2px solid #e5e7eb;">推断绩点</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows}
                        </tbody>
                    </table>
                    
                    <p style="color: #9ca3af; font-size: 12px; margin-top: 24px; text-align: center;">
                        检测时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                    </p>
                </div>
            </div>
        </body>
        </html>
        '''
        return html

    def send_grade_inference_notification(self, major: str, semester: str, inferences: list['GradeInference'], scope_label: str = "专业") -> bool:
        """发送成绩推断通知邮件"""
        if not inferences:
            logger.info('没有推断出新成绩，不发送通知')
            return True
        
        try:
            msg = MIMEMultipart('alternative')
            # 邮件标题包含变化人数和范围标签
            msg['Subject'] = f'🔍 [Main] [{scope_label}] 有 {len(inferences)} 个人的学分发生变化 - {major} ({semester})'
            msg['From'] = self.sender
            msg['To'] = self.receiver
            
            text_content = f'成绩推断通知\n{scope_label}: {major}\n学期: {semester}\n变化人数: {len(inferences)}'
            for inf in inferences:
                gpa_str = f'{inf.inferred_gpa:.2f}' if inf.inferred_gpa else '无法计算'
                text_content += f'\n- 排名 {inf.rank}: 学分 +{inf.inferred_credits:.1f}, 推断绩点 {gpa_str}'
            
            html_content = self._create_grade_inference_html(major, semester, inferences, scope_label)
            
            msg.attach(MIMEText(text_content, 'plain', 'utf-8'))
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))
            
            # 发送邮件（带重试）
            if self._send_email_with_retry(msg):
                logger.info(f'成功发送成绩推断通知 ({len(inferences)} 条)')
                return True
            else:
                return False
        except Exception as e:
            logger.error(f'发送成绩推断通知失败: {e}')
            return False
