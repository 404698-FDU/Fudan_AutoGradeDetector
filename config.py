"""
配置管理模块
从环境变量加载所有配置
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件
env_path = Path(__file__).parent / '.env'
load_dotenv(env_path)


class Config:
    """应用配置类"""
    
    # 复旦账号
    FUDAN_USERNAME: str = os.getenv('FUDAN_USERNAME', '')
    FUDAN_PASSWORD: str = os.getenv('FUDAN_PASSWORD', '')
    
    # 教务系统 URL
    GRADE_SYSTEM_URL: str = 'https://fdjwgl.fudan.edu.cn/student/'
    GPA_RANKING_URL: str = os.getenv('GPA_RANKING_URL', 'https://fdjwgl.fudan.edu.cn/student/for-std/grade/my-gpa/search-index/463952')
    UIS_LOGIN_URL: str = 'https://id.fudan.edu.cn'
    
    # 邮件配置
    SMTP_SERVER: str = os.getenv('SMTP_SERVER', '')
    SMTP_PORT: int = int(os.getenv('SMTP_PORT', '465'))
    SMTP_SSL: bool = os.getenv('SMTP_SSL', 'true').lower() == 'true'
    EMAIL_SENDER: str = os.getenv('EMAIL_SENDER', '')
    EMAIL_PASSWORD: str = os.getenv('EMAIL_PASSWORD', '')
    EMAIL_RECEIVER: str = os.getenv('EMAIL_RECEIVER', '')
    
    # 检测配置
    CHECK_INTERVAL_MINUTES: int = int(os.getenv('CHECK_INTERVAL_MINUTES', '15'))
    HEADLESS: bool = os.getenv('HEADLESS', 'true').lower() == 'true'
    
    # 学期配置
    CURRENT_SEMESTER: str = os.getenv('CURRENT_SEMESTER', '')  # 如 2024-2025-1
    INFER_GRADES: bool = os.getenv('INFER_GRADES', 'true').lower() == 'true'
    # P/NP 课程自动识别（关闭后，推断绩点接近0的情况不会被标记为P/NP，避免误判）
    INFER_PNP: bool = os.getenv('INFER_PNP', 'true').lower() == 'true'

    # 院系监控配置
    ENABLE_DEPARTMENT_MONITORING: bool = os.getenv('ENABLE_DEPARTMENT_MONITORING', 'false').lower() == 'true'
    DEPARTMENT_NAME: str = os.getenv('DEPARTMENT_NAME', '院系')
    
    
    # 数据库路径
    DB_PATH: Path = Path(__file__).parent / 'grades.db'
    
    @classmethod
    def validate(cls) -> list[str]:
        """验证配置是否完整，返回缺失的配置项"""
        errors = []
        if not cls.FUDAN_USERNAME:
            errors.append('FUDAN_USERNAME')
        if not cls.FUDAN_PASSWORD:
            errors.append('FUDAN_PASSWORD')
        if not cls.EMAIL_SENDER:
            errors.append('EMAIL_SENDER')
        if not cls.EMAIL_PASSWORD:
            errors.append('EMAIL_PASSWORD')
        if not cls.EMAIL_RECEIVER:
            errors.append('EMAIL_RECEIVER')
        return errors
