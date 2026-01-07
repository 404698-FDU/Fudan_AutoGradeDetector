# 复旦大学成绩自动检测器 (Fudan AutoGradeDetector)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

自动监控复旦大学教务管理系统的课程成绩，出分后通过邮件通知。支持本学期成绩推断、专业总排名监控及院系排名监控。

## ✨ 功能特点

- 🔐 **自动登录**: 模拟复旦统一身份认证系统 (UIS) 登录，支持重试与异常处理
- � **多维监控**:
    - **个人成绩**: 监控新出的课程成绩
    - **专业排名**: 监控专业累计 GPA 排名变化
    - **院系排名**: 支持监控院系级别的 GPA 排名（需在配置开启）
- 🧠 **智能推断**: 
    - 利用全员排名数据的学分变动，**推测他人的新出课程成绩**
    - 支持 **P/NP (通过/不通过)** 课程的智能识别
    - 采用双轮匹配算法，准确率高，自动过滤异常推断
- 📧 **邮件通知**: 支持多种邮件服务商 (QQ/Gmail/163/Outlook 等)
- 💾 **本地存储**: 使用 SQLite 数据库保存历史记录，支持查阅历史数据
- 🔄 **稳健运行**: 网络请求失败自动指数退避重试，去除了硬编码 fallback 以保障安全

## 🚀 快速开始

### 1. 安装依赖

确保已安装 Python 3.8+。

```bash
git clone https://github.com/yourusername/AutoGradeDetector.git
cd AutoGradeDetector

pip install -r requirements.txt

# 安装 Playwright 浏览器内核
playwright install chromium
```

### 2. 配置

复制配置文件模板并编辑：

```bash
cp .env.example .env
```

编辑 `.env` 文件，填入你的配置信息：

```ini
# ---复旦大学账号---
FUDAN_USERNAME=你的学号
FUDAN_PASSWORD=你的密码

# ---邮件通知配置 (以QQ邮箱为例)---
SMTP_SERVER=smtp.qq.com
SMTP_PORT=465
SMTP_SSL=true
EMAIL_SENDER=你的邮箱@qq.com
EMAIL_PASSWORD=邮箱授权码
EMAIL_RECEIVER=接收通知的邮箱

# ---监控配置---
CHECK_INTERVAL_MINUTES=15    # 检测间隔(分钟)
HEADLESS=true               # 是否隐藏浏览器窗口 (调试时可设为 false)

# ---高级功能开关---
CURRENT_SEMESTER=           # 指定学期(如 2025-2026-1)，留空自动获取
INFER_GRADES=true           # 开启成绩推断功能
INFER_PNP=true              # 开启 P/NP 课程识别
ENABLE_DEPARTMENT_MONITORING=false # 开启院系排名监控 (实验性功能)
DEPARTMENT_NAME=软件学院    # 你的院系名称 (仅当开启院系监控时需要)
```

### 3. 运行

```bash
# 单次检测 (建议首次运行使用，确保配置正确)
python main.py

# 持续监控模式 (按配置文件间隔循环运行)
python main.py --loop

# 常用命令
python main.py --test-email      # 测试邮件发送
python main.py --show            # 显示已保存的个人成绩
python main.py --show-ranking    # 显示个人排名历史记录
python main.py --show-inferred   # 显示推断出的他人成绩记录
python main.py --show-department # 显示院系排名快照 (需开启院系监控)

# 调试命令
python main.py --no-headless     # 显示浏览器界面运行
python main.py -v                # 输出详细调试日志
```

## 🧠 成绩推断原理

本程序利用 **排名系统的数据快照对比** 来推测他人的成绩更新：

1.  **数据抓取**: 定期抓取全专业/院系的 GPA 排名榜单（含 GPA、已修学分）。
2.  **双轮匹配算法**:
    -   **第一轮 (精确匹配)**: 优先匹配 GPA 和学分完全未变的学生，排除无变动人员。
    -   **第二轮 (推断匹配)**: 在剩余人员中，寻找 **学分增加** 的匹配对。
3.  **优先级策略**: 
    -   优先匹配 **整数学分变化** (如 +2.0, +3.0) 的方案（多数课程为整数学分）。
    -   其次匹配 **小数学分变化** (如 +0.5) 的方案。
4.  **合理性校验**:
    -   推断出的绩点必须落在 `[1.25, 4.05]` (正常通过) 或 `[-0.1, 0.1]` (挂科/PNP) 区间内，否则视为无效匹配，避免误报。

## 📧 邮箱配置指南

### QQ 邮箱
1. 登录 [QQ 邮箱网页版](https://mail.qq.com/) -> 设置 -> 账户。
2. 开启 "POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV服务"。
3. 生成授权码，填入 `EMAIL_PASSWORD`。

### Gmail
1. Google 账号开启 [两步验证](https://myaccount.google.com/security)。
2. 生成 [应用专用密码](https://myaccount.google.com/apppasswords)，填入 `EMAIL_PASSWORD`。

## ⏰ 定时任务推荐

虽然支持 `--loop` 模式，但为了稳定性，推荐使用系统级定时任务：

-   **Windows**: 使用 "任务计划程序" 创建任务，执行 `python main.py`。
-   **Linux/macOS**: 使用 `crontab`。
    ```bash
    */15 * * * * cd /path/to/AutoGradeDetector && /usr/bin/python3 main.py >> run.log 2>&1
    ```

## 🤝 贡献与反馈

欢迎提交 Issue 或 Pull Request。
如果您发现 Bug 或有新功能建议，请在 GitHub Issues 中反馈。

## � 许可证

本项目采用 [MIT License](LICENSE) 开源。
