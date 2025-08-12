#!/usr/bin/env python3
"""
簡單的 Telegram Q CLI Bot
支援互動式 Q CLI 會話
"""

import os
import subprocess
import logging
import asyncio
import signal
import re
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
import pexpect

# 載入 .env 檔案
load_dotenv()

# 從環境變數讀取配置
BOT_TOKEN = os.getenv('BOT_TOKEN')
ALLOWED_USERS = [int(x.strip()) for x in os.getenv('ALLOWED_USERS', '').split(',') if x.strip()]
Q_CLI_PATH = os.getenv('Q_CLI_PATH', '')
TIMEOUT = int(os.getenv('TIMEOUT', '30'))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 儲存用戶的互動式會話
user_sessions = {}

async def send_long_message(update: Update, command: str, content: str):
    """分割並發送長訊息"""
    # Telegram 訊息限制是 4096 字符，但我們要留一些空間給格式化
    max_content_length = 3800  # 留空間給命令和格式化字符
    
    if len(content) <= max_content_length:
        # 短訊息直接發送
        terminal_output = f"```\n$ {command}\n\n{content}\n```"
        await update.message.reply_text(terminal_output, parse_mode='Markdown')
        return
    
    # 長訊息需要分割
    # 首先發送命令和開始部分
    first_part = content[:max_content_length]
    # 找到最後一個完整行的位置
    last_newline = first_part.rfind('\n')
    if last_newline > 0:
        first_part = first_part[:last_newline]
    
    terminal_output = f"```\n$ {command}\n\n{first_part}\n```"
    await update.message.reply_text(terminal_output, parse_mode='Markdown')
    
    # 發送剩餘部分
    remaining = content[len(first_part):].lstrip('\n')
    part_number = 2
    
    while remaining:
        # 計算這一部分的內容
        part_content = remaining[:max_content_length]
        
        # 找到最後一個完整行
        if len(remaining) > max_content_length:
            last_newline = part_content.rfind('\n')
            if last_newline > 0:
                part_content = part_content[:last_newline]
        
        # 格式化並發送
        part_output = f"```\n(續 {part_number})\n\n{part_content}\n```"
        await update.message.reply_text(part_output, parse_mode='Markdown')
        
        # 準備下一部分
        remaining = remaining[len(part_content):].lstrip('\n')
        part_number += 1
        
        # 避免無限循環
        if part_number > 10:  # 最多分割成10個訊息
            if remaining:
                await update.message.reply_text(f"```\n(剩餘內容過長，已省略 {len(remaining)} 字符)\n```", parse_mode='Markdown')
            break

def clean_ansi_codes(text):
    """清理 ANSI 轉義序列和格式化輸出"""
    if not text:
        return text
    
    # 移除 ANSI 轉義序列
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    text = ansi_escape.sub('', text)
    
    # 移除其他控制字符
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    
    # 移除 Unicode 繪圖字符（進度條、框線等）
    text = re.sub(r'[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⢠⣶⣦⠀⡀⢀⣤⣿⣷⡆⠋⠉⠻⣧⠈⠛⣻⡿⢸⡇⢹⣇⣼⡏⣰⠃⢰⠇⣿⡄⣾⠛⣿⠀⣠⣾⡋⣀⡀⣄⣠⣤⣶⣦⣤⣤⡀⣄⠀⣤⣤⣤⣄⣤⣤⣤⣤⣤⣤⡀⣀⣤⣤⣀⠀⢠⡀⣀⣤⣄⡀⠀⠀⠀⠀⠀⢠⣿⠋⠀⠀⠙⣿⡆⠀⣼⠇⠀⣿⡄⠀⢸⣿⠛⠉⠻⣿⠛⠉⠛⣿⠀⠘⠛⠉⠉⠻⣧⠈⠛⠛⠛⣻⡿⢀⣾⠛⠉⠻⣷⡀⢸⡟⠛⠉⢻⣷⠀⠀⠀⠀⠀⣼⡏⠀⠀⠀⠀⢸⣿⠀⢰⣿⣤⣤⣼⣷⠀⢸⣿⠀⠀⣿⠀⠀⣿⠀⢀⣴⣶⣶⣶⣿⠀⠀⣠⣾⠋⠀⢸⣿⠀⠀⣿⡇⢸⡇⠀⢸⣿⠀⠀⠀⠀⠀⢹⣇⠀⠀⠀⠀⢸⡿⢀⣿⠋⠉⠉⠉⢻⣇⢸⣿⠀⠀⣿⠀⠀⣿⠀⣿⡀⠀⣠⣿⠀⢀⣴⣋⣀⣀⣀⡀⣿⣄⣀⣠⣿⠃⢸⡇⠀⢸⣿⠀⠀⠀⠀⠀⢿⣦⣀⣀⣀⣴⡿⠃⠚⠛⠋⠀⠀⠀⠘⠛⠛⠘⠛⠛⠀⠀⠛⠛⠀⠀⠛⠛⠀⠙⠻⠿⠟⠋⠛⠛⠘⠛⠛⠛⠛⠛⠛⠃⠈⠛⠿⠿⠿⠛⠁⠀⠘⠛⠃⠀⠘⠛⠛⠀⠀⠀⠀⠀⠙⠛⠿⢿⣿⣋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠛⠿⢿⡧╭─╮│╰╯━┃┏┓┗┛┣┫┳┻╋]', '', text)
    
    # 移除框線字符
    text = re.sub(r'[╭╮╯╰─│┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬]', '', text)
    
    # 移除多餘的空行
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    
    # 清理開頭和結尾的空白
    text = text.strip()
    
    return text

def find_q_cli():
    """自動尋找 Q CLI"""
    if Q_CLI_PATH and os.path.exists(Q_CLI_PATH):
        return Q_CLI_PATH
    
    # 常見路徑
    paths = ['/usr/local/bin/q', '/usr/bin/q', '~/bin/q', './q']
    for path in paths:
        expanded = os.path.expanduser(path)
        if os.path.exists(expanded) and os.access(expanded, os.X_OK):
            return expanded
    
    # 使用 which 命令
    try:
        result = subprocess.run(['which', 'q'], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
    except:
        pass
    
    return None

def check_user_permission(user_id):
    """檢查用戶權限"""
    if not ALLOWED_USERS:  # 空列表表示允許所有用戶
        return True
    return user_id in ALLOWED_USERS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """開始命令"""
    if not check_user_permission(update.effective_user.id):
        await update.message.reply_text("❌ 沒有權限使用此機器人")
        return
    
    msg = """🤖 Q CLI Telegram Bot

命令：
/start - 顯示此訊息
/status - 檢查 Q CLI 狀態
/q <命令> - 執行 Q CLI 命令
/chat - 開始互動式 Q CLI 會話
/exit - 結束互動式會話

範例：
/q --version
/q "SELECT * FROM data.csv LIMIT 5"
/chat - 進入互動模式

也可以直接發送 "q 命令"
"""
    await update.message.reply_text(msg)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """檢查狀態"""
    if not check_user_permission(update.effective_user.id):
        await update.message.reply_text("❌ 沒有權限")
        return
    
    q_path = find_q_cli()
    if not q_path:
        await update.message.reply_text("❌ 找不到 Q CLI")
        return
    
    try:
        result = subprocess.run([q_path, '--version'], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            await update.message.reply_text(f"✅ Q CLI 正常\n路徑: {q_path}\n{result.stdout.strip()}")
        else:
            await update.message.reply_text(f"❌ Q CLI 錯誤: {result.stderr}")
    except Exception as e:
        await update.message.reply_text(f"❌ 錯誤: {e}")

async def execute_q(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """執行 Q CLI 命令"""
    if not check_user_permission(update.effective_user.id):
        await update.message.reply_text("❌ 沒有權限")
        return
    
    q_path = find_q_cli()
    if not q_path:
        await update.message.reply_text("❌ 找不到 Q CLI")
        return
    
    if not context.args:
        await update.message.reply_text("❌ 請提供命令\n範例: /q --version")
        return
    
    command = ' '.join(context.args)
    logger.info(f"用戶 {update.effective_user.id} 執行: {command}")
    
    try:
        result = subprocess.run(
            f"{q_path} {command}",
            shell=True,
            capture_output=True,
            text=True,
            timeout=TIMEOUT
        )
        
        if result.returncode == 0:
            output = result.stdout.strip()
            if not output:
                output = "命令執行成功，無輸出"
            if len(output) > 4000:
                output = output[:4000] + "\n...(輸出過長)"
            await update.message.reply_text(f"✅ 執行成功:\n```\n{output}\n```", parse_mode='Markdown')
        else:
            error = result.stderr.strip()
            if not error:
                error = f"命令執行失敗，返回碼: {result.returncode}"
            if len(error) > 4000:
                error = error[:4000] + "\n...(錯誤過長)"
            await update.message.reply_text(f"❌ 執行失敗:\n```\n{error}\n```", parse_mode='Markdown')
            
    except subprocess.TimeoutExpired:
        try:
            await update.message.reply_text(f"❌ 命令超時 ({TIMEOUT}秒)")
        except:
            logger.error("無法發送超時訊息")
    except Exception as e:
        try:
            await update.message.reply_text(f"❌ 錯誤: {e}")
        except:
            logger.error(f"無法發送錯誤訊息: {e}")

async def start_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """開始互動式 Q CLI 會話"""
    user_id = update.effective_user.id
    
    if not check_user_permission(user_id):
        await update.message.reply_text("❌ 沒有權限")
        return
    
    q_path = find_q_cli()
    if not q_path:
        await update.message.reply_text("❌ 找不到 Q CLI")
        return
    
    # 標記用戶進入互動模式（不再需要實際的 pexpect 會話）
    user_sessions[user_id] = "active"
    
    await update.message.reply_text(
        "🚀 互動式 Q CLI 會話已啟動！\n\n"
        "現在你可以直接發送命令，例如：\n"
        "• Hello\n"
        "• How to create a Lambda function?\n"
        "• What is AWS S3?\n\n"
        "使用 /exit 結束會話"
    )
    
    logger.info(f"用戶 {user_id} 啟動互動式會話")

async def exit_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """結束互動式會話"""
    user_id = update.effective_user.id
    
    if user_id in user_sessions:
        del user_sessions[user_id]
        await update.message.reply_text("✅ 互動式會話已結束")
    else:
        await update.message.reply_text("❌ 沒有活躍的互動式會話")

async def handle_interactive_command(update: Update, command: str):
    """處理互動式命令 - 使用簡化的非互動模式"""
    user_id = update.effective_user.id
    
    # 不再依賴會話，直接使用非互動模式
    q_path = find_q_cli()
    if not q_path:
        await update.message.reply_text("❌ 找不到 Q CLI")
        return
    
    thinking_message = None
    
    try:
        logger.info(f"用戶 {user_id} 執行互動式命令: {command}")
        
        # 根據命令類型發送不同的進度訊息
        if any(keyword in command.lower() for keyword in ['eks', 'kubernetes', 'cluster']):
            thinking_message = await update.message.reply_text("🔍 正在查詢 EKS 集群資訊...")
        elif any(keyword in command.lower() for keyword in ['ec2', 'instance', '實例']):
            thinking_message = await update.message.reply_text("🖥️ 正在查詢 EC2 實例...")
        elif any(keyword in command.lower() for keyword in ['rds', 'database', '資料庫']):
            thinking_message = await update.message.reply_text("🗄️ 正在查詢 RDS 資料庫...")
        elif any(keyword in command.lower() for keyword in ['lambda', 'function']):
            thinking_message = await update.message.reply_text("⚡ 正在查詢 Lambda 函數...")
        elif any(keyword in command.lower() for keyword in ['s3', 'bucket', '儲存']):
            thinking_message = await update.message.reply_text("🪣 正在查詢 S3 儲存...")
        else:
            thinking_message = await update.message.reply_text("🤔 正在思考...")
        
        # 使用非互動模式執行命令
        process = await asyncio.create_subprocess_shell(
            f'echo "{command}" | {q_path} chat --non-interactive',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, 'NO_COLOR': '1', 'TERM': 'dumb'}
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)  # 增加到2分鐘
        except asyncio.TimeoutError:
            process.kill()
            await thinking_message.delete()
            await update.message.reply_text("⏰ 命令執行超時")
            return
        
        # 刪除思考訊息
        try:
            await thinking_message.delete()
        except:
            pass
        
        # 處理輸出
        if stdout:
            output = stdout.decode('utf-8', errors='ignore')
            output = clean_ansi_codes(output)
            
            # 更徹底的清理
            lines = output.split('\n')
            cleaned_lines = []
            found_response = False
            
            for line in lines:
                line = line.strip()
                
                # 跳過空行
                if not line:
                    continue
                
                # 跳過系統訊息和歡迎內容
                skip_patterns = [
                    "welcome to amazon q", "you can specify", "help all commands",
                    "ctrl +", "fuzzy search", "you are chatting with", "mcp server",
                    "servers still loading", "did you know", "enable custom tools",
                    "learn more with", "/help", "new lines", "all commands"
                ]
                
                if any(pattern in line.lower() for pattern in skip_patterns):
                    continue
                
                # 檢查是否是實際回應的開始（包含 ">" 提示符）
                if ">" in line:
                    found_response = True
                    # 提取 > 後面的內容
                    response_part = line.split(">", 1)[-1].strip()
                    if response_part:
                        cleaned_lines.append(response_part)
                    continue
                
                # 如果已經找到回應開始，收集後續內容
                if found_response:
                    # 跳過工具使用提示
                    if any(tool_pattern in line.lower() for tool_pattern in [
                        "using tool:", "running aws cli", "completed in", "service name:", 
                        "operation name:", "parameters:", "profile name:", "region:", "label:"
                    ]):
                        continue
                    
                    cleaned_lines.append(line)
            
            # 組合最終輸出
            final_output = '\n'.join(cleaned_lines).strip()
            
            if final_output:
                # 分割長訊息
                await send_long_message(update, command, final_output)
            else:
                await update.message.reply_text("```\n$ " + command + "\n\n(無輸出)\n```", parse_mode='Markdown')
        
        elif stderr:
            error = stderr.decode('utf-8', errors='ignore')
            error = clean_ansi_codes(error)
            if error.strip():
                await update.message.reply_text(f"❌ 錯誤: {error[:1000]}")
            else:
                await update.message.reply_text("✅ 命令已執行")
        else:
            await update.message.reply_text("✅ 命令已執行，無輸出")
            
    except Exception as e:
        # 確保刪除思考訊息
        if thinking_message:
            try:
                await thinking_message.delete()
            except:
                pass
        
        logger.error(f"互動式命令錯誤: {e}")
        await update.message.reply_text(f"❌ 命令執行錯誤: {e}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """處理文字訊息"""
    user_id = update.effective_user.id
    
    if not check_user_permission(user_id):
        return
    
    text = update.message.text.strip()
    
    # 如果用戶有活躍的互動式會話，直接發送到會話
    if user_id in user_sessions:
        await handle_interactive_command(update, text)
        return
    
    # 如果以 "q " 開頭，當作命令執行
    if text.lower().startswith('q '):
        command = text[2:].strip()
        # 使用 shlex 來正確分割命令參數，處理引號
        import shlex
        try:
            context.args = shlex.split(command)
        except ValueError:
            # 如果分割失敗，使用簡單分割
            context.args = command.split()
        await execute_q(update, context)
    else:
        await update.message.reply_text(
            "💡 使用方式:\n"
            "• /q <命令> - 執行單次命令\n"
            "• /chat - 開始互動式會話\n"
            "• q <命令> - 執行單次命令\n\n"
            "範例:\n"
            "/q --version\n"
            "q \"SELECT * FROM data.csv\""
        )

def cleanup_sessions():
    """清理所有會話"""
    for user_id, session in user_sessions.items():
        try:
            session.terminate()
        except:
            pass
    user_sessions.clear()

def signal_handler(signum, frame):
    """信號處理器"""
    print("\n🛑 收到停止信號，清理會話...")
    cleanup_sessions()
    exit(0)

def main():
    """主程式"""
    if not BOT_TOKEN:
        print("❌ 請先在 .env 檔案中設定 BOT_TOKEN")
        print("1. 在 Telegram 搜尋 @BotFather")
        print("2. 發送 /newbot 創建機器人")
        print("3. 複製 Token 到 .env 檔案")
        return
    
    # 檢查 pexpect
    try:
        import pexpect
    except ImportError:
        print("❌ 請安裝 pexpect: pip3 install pexpect")
        return
    
    # 檢查 Q CLI
    q_path = find_q_cli()
    if not q_path:
        print("⚠️  警告: 找不到 Q CLI")
        print("請確保已安裝 Q CLI: https://github.com/harelba/q")
    else:
        print(f"✅ 找到 Q CLI: {q_path}")
    
    # 設定信號處理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 創建機器人
    app = Application.builder().token(BOT_TOKEN).build()
    
    # 添加處理器
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("q", execute_q))
    app.add_handler(CommandHandler("chat", start_chat))
    app.add_handler(CommandHandler("exit", exit_chat))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("🚀 Telegram Q CLI Bot 啟動中...")
    print(f"允許的用戶: {ALLOWED_USERS if ALLOWED_USERS else '所有用戶'}")
    print("💡 支援互動式會話！使用 /chat 開始")
    
    try:
        # 啟動
        app.run_polling()
    except KeyboardInterrupt:
        print("\n🛑 機器人停止")
    finally:
        cleanup_sessions()

if __name__ == '__main__':
    main()