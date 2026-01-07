"""
成绩检测核心模块
使用 Playwright 自动登录并获取成绩
"""
import logging
import re
import time
from functools import wraps
from typing import Optional, Callable, TypeVar, Any
from playwright.sync_api import sync_playwright, Page, Browser, TimeoutError as PlaywrightTimeout

from database import Grade, GradeDatabase, GpaRanking, RankingDatabase, MajorStudentRanking, GradeInference
from email_notifier import EmailNotifier
from config import Config

logger = logging.getLogger(__name__)

# 重试配置
MAX_RETRIES = 3
RETRY_BASE_DELAY = 10  # 秒

T = TypeVar('T')


def retry_on_failure(max_retries: int = MAX_RETRIES, base_delay: int = RETRY_BASE_DELAY):
    """
    重试装饰器，用于网络操作
    使用指数退避策略
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_error = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (PlaywrightTimeout, Exception) as e:
                    last_error = e
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f'{func.__name__} 失败 (尝试 {attempt + 1}/{max_retries}): {e}')
                    
                    if attempt < max_retries - 1:
                        logger.info(f'将在 {delay} 秒后重试...')
                        time.sleep(delay)
            
            logger.error(f'{func.__name__} 最终失败，已重试 {max_retries} 次')
            raise last_error
        return wrapper
    return decorator


class FudanGradeDetector:
    """复旦大学成绩检测器"""
    
    def __init__(
        self,
        username: str,
        password: str,
        db: GradeDatabase,
        ranking_db: RankingDatabase,
        notifier: EmailNotifier,
        headless: bool = True
    ):
        self.username = username
        self.password = password
        self.db = db
        self.ranking_db = ranking_db
        self.notifier = notifier
        self.headless = headless
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
    
    def _login_with_retry(self, page: Page, max_retries: int = MAX_RETRIES) -> bool:
        """
        带重试机制的登录
        """
        for attempt in range(max_retries):
            try:
                result = self._login_internal(page)
                if result:
                    return True
                else:
                    logger.warning(f'登录失败 (尝试 {attempt + 1}/{max_retries})')
            except Exception as e:
                logger.warning(f'登录出错 (尝试 {attempt + 1}/{max_retries}): {e}')
            
            if attempt < max_retries - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.info(f'将在 {delay} 秒后重试登录...')
                time.sleep(delay)
                # 刷新页面准备重试
                try:
                    page.goto('about:blank')
                except:
                    pass
        
        logger.error(f'登录最终失败，已重试 {max_retries} 次')
        return False
    
    def _login_internal(self, page: Page) -> bool:
        """
        登录复旦统一身份认证系统 (id.fudan.edu.cn)
        流程: fdjwgl.fudan.edu.cn/student -> id.fudan.edu.cn -> 登录 -> 返回教务系统
        """
        try:
            logger.info('正在访问教务系统...')
            # 访问教务系统，等待初始加载完成
            page.goto(Config.GRADE_SYSTEM_URL, wait_until='load', timeout=60000)
            logger.info(f'初始页面已加载, URL: {page.url}')
            
            # 等待页面稳定和可能的 JS 重定向
            max_wait_seconds = 30
            poll_interval = 2
            waited = 0
            
            while waited < max_wait_seconds:
                page.wait_for_timeout(poll_interval * 1000)
                waited += poll_interval
                current_url = page.url
                
                # 检查是否到达登录页面
                if 'id.fudan.edu.cn' in current_url:
                    logger.info(f'已跳转到登录页面: {current_url}')
                    break
                
                # 检查是否已经登录（直接进入教务系统）
                if 'fdjwgl.fudan.edu.cn' in current_url:
                    # 尝试查找登录相关元素，判断是否需要登录
                    try:
                        page.wait_for_selector('#login-username', timeout=1000)
                        logger.info('在教务系统页面发现登录框')
                        break
                    except:
                        # 可能已经登录了
                        logger.info('可能已经登录，继续检查...')
                
                logger.debug(f'等待重定向... ({waited}s)')
            
            # 最终检查当前状态
            current_url = page.url
            logger.info(f'等待完成, 当前 URL: {current_url}')
            
            # 如果还没到登录页面，尝试直接等待登录表单
            if 'id.fudan.edu.cn' not in current_url:
                try:
                    page.wait_for_selector('#login-username', timeout=10000, state='visible')
                except PlaywrightTimeout:
                    logger.error(f'未能找到登录页面, URL: {current_url}')
                    page.screenshot(path='login_no_form.png')
                    return False
            
            # 等待用户名输入框出现 (id.fudan.edu.cn 的选择器)
            username_selectors = [
                '#login-username',
                'input[name="username"]',
                'input[placeholder*="用户名"]',
                'input[placeholder*="学工号"]',
            ]
            
            username_input = None
            for selector in username_selectors:
                try:
                    page.wait_for_selector(selector, timeout=5000, state='visible')
                    username_input = selector
                    logger.info(f'找到用户名输入框: {selector}')
                    break
                except:
                    continue
            
            if not username_input:
                logger.error('找不到用户名输入框')
                logger.info(f'当前 URL: {page.url}')
                page.screenshot(path='login_no_username.png')
                return False
            
            # 输入用户名
            page.fill(username_input, self.username)
            logger.info(f'已输入用户名: {self.username[:4]}****')
            
            # 输入密码 (id.fudan.edu.cn 的选择器)
            password_selectors = ['#login-password', 'input[name="password"]', 'input[type="password"]']
            password_filled = False
            for selector in password_selectors:
                try:
                    elem = page.query_selector(selector)
                    if elem and elem.is_visible():
                        page.fill(selector, self.password)
                        password_filled = True
                        logger.info('已输入密码')
                        break
                except:
                    continue
            
            if not password_filled:
                logger.error('找不到密码输入框')
                page.screenshot(path='login_no_password.png')
                return False
            
            # 等待登录按钮可用并点击 (id.fudan.edu.cn 的选择器)
            page.wait_for_timeout(500)
            
            login_btn_selectors = [
                'button.content_submit:not(.is-disabled)',
                'button.submitBtnColor:not([disabled])',
                'button:has-text("登录"):not([disabled])',
                'button[type="submit"]',
            ]
            
            clicked = False
            for selector in login_btn_selectors:
                try:
                    btn = page.query_selector(selector)
                    if btn and btn.is_visible():
                        btn.click()
                        clicked = True
                        logger.info(f'已点击登录按钮: {selector}')
                        break
                except Exception as e:
                    logger.debug(f'按钮 {selector} 失败: {e}')
                    continue
            
            if not clicked:
                logger.error('找不到可用的登录按钮')
                page.screenshot(path='login_btn_error.png')
                return False
            
            # 等待登录成功并跳转回教务系统
            logger.info('等待登录跳转...')
            try:
                page.wait_for_url('**/fdjwgl.fudan.edu.cn/**', timeout=30000)
                logger.info('登录成功！已进入教务系统')
                return True
            except PlaywrightTimeout:
                # 检查当前状态
                current_url = page.url
                if 'fdjwgl.fudan.edu.cn' in current_url:
                    logger.info('登录成功！已进入教务系统')
                    return True
                elif 'id.fudan.edu.cn' in current_url:
                    # 可能登录失败，检查错误信息
                    logger.error(f'登录可能失败，仍在登录页面: {current_url}')
                    page.screenshot(path='login_failed.png')
                    return False
                else:
                    logger.info(f'登录后 URL: {current_url}')
                    page.screenshot(path='login_unknown_state.png')
                    return False
            
        except PlaywrightTimeout as e:
            logger.error(f'登录超时: {e}')
            logger.info(f'当前 URL: {page.url}')
            page.screenshot(path='login_error.png')
            return False
        except Exception as e:
            logger.error(f'登录失败: {e}')
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def _login(self, page: Page) -> bool:
        """登录入口（使用重试机制）"""
        return self._login_with_retry(page)

    
    def _navigate_to_grades_with_retry(self, page: Page, max_retries: int = MAX_RETRIES) -> bool:
        """带重试机制的成绩页面导航"""
        for attempt in range(max_retries):
            try:
                result = self._navigate_to_grades_internal(page)
                if result:
                    return True
            except Exception as e:
                logger.warning(f'导航失败 (尝试 {attempt + 1}/{max_retries}): {e}')
            
            if attempt < max_retries - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.info(f'将在 {delay} 秒后重试导航...')
                time.sleep(delay)
        
        return False
    
    def _navigate_to_grades_internal(self, page: Page) -> bool:
        """导航到成绩查询页面（内部实现）"""
        try:
            # 等待页面加载完成
            page.wait_for_load_state('networkidle', timeout=10000)
            
            # 尝试多种方式找到成绩查询入口
            # 方式1: 直接访问成绩查询 URL
            grade_url_patterns = [
                '/student/for-std/grade/sheet',
                '/student/for-std/grade',
                '/student/integratedQuery/result/thisTermScores/index',
            ]
            
            for pattern in grade_url_patterns:
                try:
                    test_url = f'https://fdjwgl.fudan.edu.cn{pattern}'
                    page.goto(test_url, wait_until='networkidle', timeout=15000)
                    # 检查是否成功加载成绩页面
                    if '成绩' in page.content() or 'grade' in page.url.lower():
                        logger.info(f'成功访问成绩页面: {pattern}')
                        return True
                except:
                    continue
            
            # 方式2: 通过菜单导航
            logger.info('尝试通过菜单导航到成绩页面...')
            
            # 查找包含"成绩"的链接或菜单项
            grade_links = page.query_selector_all('a:has-text("成绩"), span:has-text("成绩")')
            for link in grade_links:
                try:
                    link.click()
                    page.wait_for_load_state('networkidle', timeout=5000)
                    if '成绩' in page.content():
                        logger.info('通过菜单成功导航到成绩页面')
                        return True
                except:
                    continue
            
            logger.warning('未能自动导航到成绩页面，将尝试解析当前页面')
            return True  # 继续尝试解析
            
        except Exception as e:
            logger.error(f'导航到成绩页面失败: {e}')
            return False
    
    def _navigate_to_grades(self, page: Page) -> bool:
        """导航入口（使用重试机制）"""
        return self._navigate_to_grades_with_retry(page)
    
    def _parse_grades(self, page: Page) -> list[Grade]:
        """从页面解析成绩数据"""
        grades = []
        
        try:
            # 等待成绩表格加载
            page.wait_for_selector('table', timeout=10000)
            
            # 使用 JavaScript 提取表格数据，包括表头信息
            grade_data = page.evaluate('''
                () => {
                    const results = [];
                    const tables = document.querySelectorAll('table');
                    
                    for (const table of tables) {
                        // 获取表头
                        const headerRow = table.querySelector('thead tr, tr:first-child');
                        const headers = headerRow ? 
                            Array.from(headerRow.querySelectorAll('th, td')).map(h => h.textContent.trim()) : [];
                        
                        // 获取数据行
                        const rows = table.querySelectorAll('tbody tr, tr');
                        
                        for (let i = 0; i < rows.length; i++) {
                            const row = rows[i];
                            const cells = row.querySelectorAll('td');
                            
                            // 跳过表头行和空行
                            if (cells.length < 4) continue;
                            
                            const rowData = Array.from(cells).map(cell => cell.textContent.trim());
                            
                            // 检查是否包含成绩数据（包括 P、NP、数字、字母等级）
                            const hasGrade = rowData.some(text => 
                                /^(\\d{1,3}(\\.\\d+)?|[A-F][+-]?|P|NP|优秀|良好|中等|及格|不及格|通过|不通过|缓考|缺考)$/i.test(text)
                            );
                            
                            if (hasGrade) {
                                results.push({
                                    headers: headers,
                                    cells: rowData,
                                    cellCount: rowData.length
                                });
                            }
                        }
                    }
                    
                    return results;
                }
            ''')
            
            logger.info(f'从页面提取到 {len(grade_data)} 条原始数据')
            
            # 调试：输出第一行原始数据
            if grade_data and len(grade_data) > 0:
                first = grade_data[0]
                logger.info(f'表头: {first.get("headers", [])}')
                logger.info(f'第一行数据: {first.get("cells", [])}')
            
            # 解析提取的数据
            for item in grade_data:
                cells = item.get('cells', [])
                headers = item.get('headers', [])
                
                if len(cells) < 4:
                    continue
                
                # 尝试通过表头识别列索引
                col_map = self._identify_columns(headers)
                
                # 提取各字段
                course_id = ''
                course_name = ''
                score = ''
                grade_point = None
                
                # 如果有表头映射，使用映射
                if col_map:
                    logger.debug(f'列映射: {col_map}')
                    if col_map.get('code') is not None and col_map['code'] < len(cells):
                        course_id = cells[col_map['code']]
                    if col_map.get('name') is not None and col_map['name'] < len(cells):
                        course_name = cells[col_map['name']]
                    if col_map.get('score') is not None and col_map['score'] < len(cells):
                        score = cells[col_map['score']]
                    if col_map.get('gpa') is not None and col_map['gpa'] < len(cells):
                        try:
                            grade_point = float(cells[col_map['gpa']])
                        except:
                            pass
                
                # 如果没有表头映射或未提取到数据，使用备用解析逻辑
                if not col_map or not course_name:
                    # 遍历所有单元格，智能识别各字段
                    for i, cell in enumerate(cells):
                        cell_clean = cell.strip()
                        
                        # 识别课程代码（带点的字母数字组合，如 COMP120006.16）
                        if not course_id and re.match(r'^[A-Z]{2,}[0-9]+(\.[0-9]+)?$', cell_clean):
                            course_id = cell_clean
                            continue
                        
                        # 识别成绩
                        if not score and re.match(r'^(\d{1,3}(\.\d+)?|[A-F][+-]?|P|NP|优秀|良好|中等|及格|不及格|通过|不通过)$', cell_clean, re.IGNORECASE):
                            score = cell_clean
                            continue
                        
                        # 识别绩点（0-4.5之间的小数）
                        if grade_point is None:
                            try:
                                val = float(cell_clean)
                                if 0 <= val <= 4.5:
                                    grade_point = val
                                    continue
                            except:
                                pass
                        
                        # 识别课程名称（中文为主的较长字符串）
                        if not course_name and len(cell_clean) > 2 and re.search(r'[\u4e00-\u9fff]', cell_clean):
                            course_name = cell_clean
                
                # 如果课程名称为空，使用课程代码
                if not course_name and course_id:
                    course_name = course_id
                
                # 验证并添加成绩
                if course_name and score:
                    grades.append(Grade(
                        course_id=course_id,
                        course_name=course_name,
                        score=score.upper() if len(score) <= 2 else score,
                        semester=self._get_current_semester(),
                        grade_point=grade_point
                    ))
                    logger.debug(f'解析成绩: {course_name} ({course_id}) - 成绩:{score} 绩点:{grade_point}')
            
            logger.info(f'成功解析 {len(grades)} 门课程成绩')
            return grades
            
        except Exception as e:
            logger.error(f'解析成绩失败: {e}')
            import traceback
            logger.debug(traceback.format_exc())
            page.screenshot(path='parse_error.png')
            return []
    
    def _identify_columns(self, headers: list[str]) -> dict:
        """根据表头识别各列的索引"""
        if not headers:
            return {}
        
        col_map = {}
        
        for i, header in enumerate(headers):
            h = header.strip()
            # 课程代码/课程序号 - 取后者作为完整代码
            if h == '课程序号':
                col_map['code'] = i
            elif h == '课程代码' and 'code' not in col_map:
                col_map['code'] = i
            # 课程名称
            elif '名称' in h or '课程名' in h:
                col_map['name'] = i
            # 学分
            elif '学分' in h:
                col_map['credit'] = i
            # 成绩（但不含绩点）
            elif h == '成绩' or (h.endswith('成绩') and '绩点' not in h):
                col_map['score'] = i
            # 绩点
            elif '绩点' in h:
                col_map['gpa'] = i
        
        logger.debug(f'列映射结果: {col_map} (表头: {headers})')
        return col_map
    
    def _get_current_semester(self) -> str:
        """获取当前学期标识"""
        from datetime import datetime
        now = datetime.now()
        year = now.year
        month = now.month
        
        # 9-2 月为第一学期，3-8 月为第二学期
        if month >= 9 or month <= 2:
            if month >= 9:
                return f'{year}-{year+1}-1'
            else:
                return f'{year-1}-{year}-1'
        else:
            return f'{year-1}-{year}-2'

    def _fetch_ranking_with_retry(self, page: Page, max_retries: int = MAX_RETRIES) -> Optional[GpaRanking]:
        """带重试机制的排名获取"""
        for attempt in range(max_retries):
            try:
                result = self._fetch_ranking_internal(page)
                if result:
                    return result
                logger.warning(f'获取排名返回空 (尝试 {attempt + 1}/{max_retries})')
            except Exception as e:
                logger.warning(f'获取排名失败 (尝试 {attempt + 1}/{max_retries}): {e}')
            
            if attempt < max_retries - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.info(f'将在 {delay} 秒后重试获取排名...')
                time.sleep(delay)
        
        return None
    
    def _fetch_ranking_internal(self, page: Page) -> Optional[GpaRanking]:
        """抓取并解析绩点排名（内部实现）"""
        try:
            logger.info('正在获取绩点排名...')
            page.goto(Config.GPA_RANKING_URL, wait_until='networkidle', timeout=60000)
            
            # 等待排名信息加载
            page.wait_for_selector('text=绩点', timeout=20000)
            
            # 增加一点等待，确保 Vue 渲染完成
            page.wait_for_timeout(2000)
            
            # 使用 JS 提取排名信息
            ranking_data = page.evaluate('''
                () => {
                    const text = document.body.innerText;
                    
                    // 尝试匹配 "绩点: 3.75" 或 "平均学分绩点 \n 3.75"
                    // 匹配数字，可能是小数
                    const gpaMatch = text.match(/(?:平均学分绩点|绩点)\s*[\n\r\s]*([0-9.]+)/);
                    const gpa = gpaMatch ? parseFloat(gpaMatch[1]) : 0.0;
                    
                    // 尝试匹配 "专业排名 \n 46"
                    const rankMatch = text.match(/(?:专业排名|排名)\s*[\n\r\s]*(\d+)/);
                    const ranking = rankMatch ? parseInt(rankMatch[1]) : 0;
                    
                    // 尝试匹配 "共 96 人"
                    const totalMatch = text.match(/共\s*(\d+)\s*人/);
                    let total = totalMatch ? parseInt(totalMatch[1]) : 0;
                    
                    // 如果没找到总人数，尝试查找排名列表长度
                    if (total === 0) {
                        try {
                            const tableRows = document.querySelectorAll('table tbody tr');
                            if (tableRows.length > 0) total = tableRows.length;
                        } catch (e) {}
                    }
                    
                    // 获取专业名称
                    const majorMatch = text.match(/(?:专业)[:：\s]*[\n\r\s]*([^\n\r0-9]+)/);
                    let major = "软件工程";
                    if (majorMatch && majorMatch[1].trim() !== "排名") {
                         major = majorMatch[1].trim();
                    }
                    
                    return { major, gpa, ranking, total };
                }
            ''')
            
            logger.info(f'获取到排名数据: {ranking_data}')
            
            if ranking_data and ranking_data['ranking'] > 0:
                return GpaRanking(
                    major=ranking_data['major'],
                    gpa=ranking_data['gpa'],
                    ranking=ranking_data['ranking'],
                    total_students=ranking_data['total'],
                    percentile=ranking_data['ranking'] / ranking_data['total'] if ranking_data['total'] > 0 else 0.0
                )
            return None
        except Exception as e:
            logger.error(f'获取排名失败: {e}')
            page.screenshot(path='ranking_error.png')
            return None
    
    def _fetch_ranking(self, page: Page) -> Optional[GpaRanking]:
        """获取排名入口（使用重试机制）"""
        return self._fetch_ranking_with_retry(page)

    def _fetch_major_rankings_with_retry(self, page: Page, semester: str = "", scope: str = "major", max_retries: int = MAX_RETRIES) -> list[MajorStudentRanking]:
        """带重试机制的全专业/院系排名获取"""
        for attempt in range(max_retries):
            try:
                result = self._fetch_major_rankings_internal(page, semester, scope)
                if result:  # 非空列表
                    return result
                logger.warning(f'获取{scope}排名返回空 (尝试 {attempt + 1}/{max_retries})')
            except Exception as e:
                logger.warning(f'获取{scope}排名失败 (尝试 {attempt + 1}/{max_retries}): {e}')
            
            if attempt < max_retries - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.info(f'将在 {delay} 秒后重试获取{scope}排名...')
                time.sleep(delay)
        
        return []
    
    def _fetch_major_rankings_internal(self, page: Page, semester: str = "", scope: str = "major") -> list[MajorStudentRanking]:
        """抓取并解析全专业/院系绩点排名榜单（内部实现）"""
        scope_name = "专业" if scope == "major" else Config.DEPARTMENT_NAME
        try:
            logger.info(f'正在获取全{scope_name}绩点排名榜单... (学期: {semester or "全部累计"})')
            # 首先确保在排名页面
            if not page.url.startswith("https://fdjwgl.fudan.edu.cn/student/for-std/grade/my-gpa/"):
                page.goto(Config.GPA_RANKING_URL, wait_until='networkidle', timeout=60000)
            
            # 使用 API 获取数据
            api_url_match = page.evaluate('''
                () => {
                    return window.performance.getEntriesByType('resource')
                        .map(r => r.name)
                        .find(name => name.includes('/student/for-std/grade/my-gpa/search?'));
                }
            ''')
            
            if not api_url_match:
                logger.warning('未能自动发现排名 API')
                # 尝试再次等待并查找 (针对加载慢的情况)
                page.wait_for_timeout(2000)
                api_url_match = page.evaluate('''
                    () => {
                        return window.performance.getEntriesByType('resource')
                            .map(r => r.name)
                            .find(name => name.includes('/student/for-std/grade/my-gpa/search?'));
                    }
                ''')
                
            if not api_url_match:
                logger.error('无法获取排名 API URL (Performance Entry Not Found)')
                return []

            # 根据 scope 调整参数
            if scope == 'department':
                # 移除 majorAssoc 参数以获取院系排名
                import re
                api_url_match = re.sub(r'&majorAssoc=[^&]*', '', api_url_match)
                logger.info('已移除 majorAssoc 参数，将获取院系排名')

            # 如果指定了学期，添加学期参数

            # 学期参数格式: startDate=YYYY-MM-DD&endDate=YYYY-MM-DD
            # 学期日期映射 (学期代码 -> 开始日期)
            semester_date_map = {
                '2025-2026-1': '2025-09-07',  # 2025-2026学年1学期
                '2024-2025-2': '2025-02-16',  # 2024-2025学年2学期
                '2024-2025-1': '2024-09-01',  # 2024-2025学年1学期
                '2024-2025-summer': '2025-06-22',  # 2024-2025学年暑假学期
            }
            
            if semester:
                # 尝试从映射中获取日期
                semester_date = semester_date_map.get(semester)
                
                if not semester_date:
                    # 如果映射中没有，尝试从页面获取
                    semester_date = self._get_semester_date_from_page(page, semester)
                
                if semester_date:
                    # 移除可能存在的旧学期参数
                    import re
                    api_url_match = re.sub(r'&startDate=[^&]*', '', api_url_match)
                    api_url_match = re.sub(r'&endDate=[^&]*', '', api_url_match)
                    # 添加新的学期参数
                    api_url_match += f"&startDate={semester_date}&endDate={semester_date}"
                    logger.info(f'使用学期日期筛选: {semester_date}')
                else:
                    logger.warning(f'无法找到学期 {semester} 对应的日期，将返回累计数据')
            
            logger.debug(f'使用的排名 API: {api_url_match}')
            
            # 在页面环境下执行 fetch 以带上 Session
            json_data = page.evaluate(f'''
                async (url) => {{
                    const resp = await fetch(url);
                    return await resp.json();
                }}
            ''', api_url_match)
            
            students = []
            if json_data and 'data' in json_data:
                # 调试：输出第一条数据的所有字段和学分范围
                if json_data['data']:
                    first_item = json_data['data'][0]
                    credits = [item.get('credit', 0) for item in json_data['data'][:10]]
                    logger.info(f'API 响应: {len(json_data["data"])} 人, 前10人学分: {credits}')
                    if semester:
                        max_credit = max(item.get('credit', 0) for item in json_data['data'])
                        if max_credit > 35:
                            logger.warning(f'学分超过35 (max={max_credit})，可能学期筛选未生效')
                        else:
                            logger.info(f'学分范围合理 (max={max_credit})，学期筛选生效')
                
                for item in json_data['data']:
                    # 获取姓名，优先尝试各个可能的字段
                    name = item.get('studentName') or item.get('name') or item.get('maskedStudentName') or "****"
                    
                    # 判断是否是自己（名字未被脱敏）
                    is_me = (name != "****" and name is not None)
                    
                    # 确定专业/院系名称
                    major_name = "软件工程"
                    if scope == 'department':
                        major_name = Config.DEPARTMENT_NAME
                    else:
                        major_name = item.get('majorName') or item.get('major') or "软件工程"

                    students.append(MajorStudentRanking(
                        rank=item.get('ranking'),
                        name=name,
                        gpa=item.get('gpa', 0.0),
                        credits=item.get('credit', 0.0),
                        major=major_name,
                        semester=semester or self._get_current_semester(),
                        is_me=is_me
                    ))
            
            logger.info(f'成功获取到 {len(students)} 名学生的{scope_name}排名数据')
            return students
        except Exception as e:
            logger.error(f'获取全{scope_name}排名失败: {e}')
            return []
    
    def _get_semester_date_from_page(self, page: Page, semester: str) -> Optional[str]:
        """从页面下拉框获取学期对应的日期"""
        try:
            # 点击下拉框打开选项
            start_selectized = page.query_selector('#startDate-selectized')
            if start_selectized:
                start_selectized.click()
                page.wait_for_timeout(500)
                
                # 获取所有选项
                options = page.evaluate('''
                    () => {
                        const dropdown = document.querySelector('.selectize-dropdown-content');
                        if (!dropdown) return [];
                        const items = dropdown.querySelectorAll('.option');
                        return Array.from(items).map(item => ({
                            value: item.getAttribute('data-value'),
                            text: item.textContent.trim()
                        }));
                    }
                ''')
                
                # 点击页面其他地方关闭下拉框
                page.click('body', position={'x': 0, 'y': 0})
                page.wait_for_timeout(200)
                
                # 查找匹配的学期
                for opt in options:
                    text = opt.get('text', '')
                    value = opt.get('value', '')
                    # 匹配学期代码 (如 2025-2026-1)
                    if semester in text or (semester.replace('-', '') in text.replace('-', '')):
                        return value
                    # 尝试解析学期代码
                    parts = semester.split('-')
                    if len(parts) >= 3:
                        year_range = f'{parts[0]}-{parts[1]}'
                        term = parts[2]
                        if year_range in text and f'{term}学期' in text:
                            return value
        except Exception as e:
            logger.debug(f'从页面获取学期日期失败: {e}')
        return None
    
    def _fetch_major_rankings(self, page: Page, semester: str = "", scope: str = "major") -> list[MajorStudentRanking]:
        """获取全专业/院系排名入口（使用重试机制）"""
        return self._fetch_major_rankings_with_retry(page, semester, scope)

    def check_major_ranking_change(self, page: Page, current_rankings: Optional[list[MajorStudentRanking]] = None, scope: str = "major") -> bool:
        """检查全专业/院系排名是否有变动"""
        scope_name = "专业" if scope == "major" else Config.DEPARTMENT_NAME
        logger.info(f'开始检查全{scope_name}排名变动...')
        
        if current_rankings is None:
            current_rankings = self._fetch_major_rankings(page, scope=scope)
            
        if not current_rankings:
            logger.warning(f'未获取到全{scope_name}排名数据')
            return False
            
        major = current_rankings[0].major if current_rankings else "软件工程"
        
        # 保存快照并获取变动详情
        # 即使是院系排名，也可以复用 major_rankings 表，只要 major 字段存的是院系名即可
        changed, previous_rankings = self.ranking_db.save_major_rankings(major, current_rankings)
        
        if changed:
            logger.info(f'发现全{scope_name}排名变动！正在发送通知...')
            # 发送通知时需传入 scope_label
            scope_label = "院系" if scope == "department" else "专业"
            self.notifier.send_major_ranking_update(major, current_rankings, previous_rankings, scope_label=scope_label)
        else:
            logger.info(f'全{scope_name}排名无变动')
            
        return changed

    def check_semester_rankings(self, page: Page, semester: str = "", scope: str = "major") -> dict:
        """
        检查指定学期的排名变动并推断成绩
        """
        scope_name = "专业" if scope == "major" else Config.DEPARTMENT_NAME
        semester = semester or Config.CURRENT_SEMESTER or self._get_current_semester()
        logger.info(f'开始检查本学期{scope_name}成绩推断 (学期: {semester})...')
        
        result = {
            'changed': False,
            'inferences': [],
            'semester': semester
        }
        
        current_rankings = self._fetch_major_rankings(page, semester, scope=scope)
        if not current_rankings:
            logger.warning(f'未能获取到当前学期{scope_name}排名数据')
            return result
        
        major = current_rankings[0].major if current_rankings else "软件工程"
        
        # 保存快照并获取推断结果
        changed, previous_rankings, inferences = self.ranking_db.save_semester_rankings(
            major, semester, current_rankings, infer_pnp=Config.INFER_PNP
        )
        
        result['changed'] = changed
        result['inferences'] = inferences
        
        if inferences:
            logger.info(f'推断出 {len(inferences)} 条成绩变动 ({scope_name}):')
            for inf in inferences:
                if getattr(inf, 'is_pnp', False):
                    gpa_str = 'P/NP课程'
                elif inf.inferred_gpa is not None:
                    gpa_str = f'{inf.inferred_gpa:.2f}'
                else:
                    gpa_str = '无法计算'
                logger.info(f'  - 排名 {inf.rank} ({inf.name}): 学分 +{inf.inferred_credits:.1f}, 推断绩点 {gpa_str}')
            
            # 发送成绩推断通知
            scope_label = "院系" if scope == "department" else "专业"
            self.notifier.send_grade_inference_notification(major, semester, inferences, scope_label=scope_label)
        else:
            logger.info(f'本学期{scope_name}暂无新成绩推断')
        
        return result

    
    def fetch_grades(self) -> list[Grade]:
        """获取所有成绩"""
        with sync_playwright() as p:
            logger.info(f'启动浏览器 (headless={self.headless})')
            browser = p.chromium.launch(headless=self.headless)
            
            try:
                context = browser.new_context(
                    viewport={'width': 1280, 'height': 720},
                    locale='zh-CN'
                )
                page = context.new_page()
                
                # 登录
                if not self._login(page):
                    logger.error('登录失败，无法获取成绩')
                    return []
                
                # 导航到成绩页面
                if not self._navigate_to_grades(page):
                    logger.error('无法访问成绩页面')
                    return []
                
                # 解析成绩
                grades = self._parse_grades(page)
                return grades
                
            finally:
                browser.close()
                logger.info('浏览器已关闭')
    
    def check_ranking_change(self, page: Page, ranking_obj: Optional[GpaRanking] = None) -> bool:
        """
        检查并通知排名变化
        如果提供了 ranking_obj，则直接使用，不再重新获取
        """
        logger.info('开始检查个人排名变化...')
        
        if ranking_obj:
            new_ranking = ranking_obj
        else:
            new_ranking = self._fetch_ranking(page)
            
        if not new_ranking:
            logger.warning('未能获取到个人排名数据')
            return False
            
        old_ranking = self.ranking_db.get_latest_ranking(new_ranking.major)
        changed = self.ranking_db.save_ranking(new_ranking)
        
        if changed:
            logger.info(f'发现排名变化: 第 {new_ranking.ranking} 名')
            self.notifier.send_ranking_notification(old_ranking, new_ranking)
        else:
            logger.info(f'个人排名没有变化 (当前第 {new_ranking.ranking}/{new_ranking.total_students} 名)')
        return changed

    def check_new_grades(self, infer_grades: bool = True) -> dict:
        """
        检查新成绩和排名变化
        
        功能1: 监控专业总绩点排名变化 -> 发送排名变化通知邮件
        功能2: 监控本学期数据变化 -> 推断他人新出课程绩点 -> 发送成绩推断通知邮件
        
        这两个功能独立运行，发送两封不同的邮件
        """
        logger.info('开始全面检查更新...')
        
        results = {
            'new_grades': [],
            'ranking_changed': False,
            'major_ranking_changed': False,
            'semester_inferences': [],
            'department_ranking_changed': False,
            'department_inferences': []
        }

        with sync_playwright() as p:
            logger.info(f'启动浏览器 (headless={self.headless})')
            browser = p.chromium.launch(headless=self.headless)
            
            try:
                context = browser.new_context(
                    viewport={'width': 1280, 'height': 720},
                    locale='zh-CN'
                )
                page = context.new_page()
                
                # 登录
                if not self._login(page):
                    logger.error('登录失败')
                    return results
                
                # 1. 检查自己的成绩
                if self._navigate_to_grades(page):
                    current_grades = self._parse_grades(page)
                    for grade in current_grades:
                        if self.db.save_grade(grade):
                            results['new_grades'].append(grade)
                            logger.info(f'发现新成绩: {grade.course_name} - {grade.score}')
                    
                    if results['new_grades']:
                        self.notifier.send_notification(results['new_grades'])
                
                # ===== 优化流程：先获取全专业累计排名 =====
                # 这样可以一次性得到：
                # 1. 自己的准确排名（用于个人排名通知）
                # 2. 全专业的排名快照（用于全专业排名变动通知）
                
                logger.info('获取全专业累计排名数据...')
                major_rankings_total = self._fetch_major_rankings(page, semester="")
                
                # 2. 检查个人排名
                # 从全专业排名中提取自己的信息，避免单独去爬取不稳定的个人排名区域
                my_ranking_info = None
                if major_rankings_total:
                    for s in major_rankings_total:
                        if s.is_me:
                            my_ranking_info = GpaRanking(
                                major=s.major,
                                gpa=s.gpa,
                                ranking=s.rank,
                                total_students=len(major_rankings_total),
                                percentile=s.rank / len(major_rankings_total) if len(major_rankings_total) > 0 else 0.0
                            )
                            break
                
                if my_ranking_info:
                    results['ranking_changed'] = self.check_ranking_change(page, ranking_obj=my_ranking_info)
                else:
                    logger.warning('在全专业排名中未找到自己的记录，将尝试回退到旧方法检查个人排名')
                    results['ranking_changed'] = self.check_ranking_change(page)
                
                # ===== 功能1: 监控专业总绩点排名变化 =====
                # 使用累计数据（不指定学期），检查全专业排名变动
                # 变动时发送「全专业排名变动通知」邮件
                logger.info('=' * 40)
                logger.info('【功能1】检查专业总绩点排名变化...')
                results['major_ranking_changed'] = self.check_major_ranking_change(page, current_rankings=major_rankings_total)
                
                # ===== 功能2: 监控本学期成绩推断 =====
                # 使用本学期数据，对比学分/绩点变化推断他人新出课程
                # 有推断结果时发送「成绩推断通知」邮件
                if infer_grades and Config.INFER_GRADES:
                    logger.info('=' * 40)
                    logger.info('【功能2】检查本学期成绩推断...')
                    semester_result = self.check_semester_rankings(page)
                    results['semester_inferences'] = semester_result['inferences']
                
                # ===== 功能3: 监控院系排名变化 =====
                if Config.ENABLE_DEPARTMENT_MONITORING:
                    logger.info('=' * 40)
                    logger.info('【功能3】检查院系总绩点排名变化...')
                    results['department_ranking_changed'] = self.check_major_ranking_change(page, scope="department")
                    
                    if infer_grades and Config.INFER_GRADES:
                        logger.info('=' * 40)
                        logger.info('【功能4】检查本学期院系成绩推断...')
                        dept_semester_result = self.check_semester_rankings(page, scope="department")
                        results['department_inferences'] = dept_semester_result['inferences']
                
            finally:
                browser.close()
                logger.info('浏览器已关闭')
        
        return results
    
    def run_once(self) -> dict:
        """运行一次检测"""
        return self.check_new_grades()

