"""
背单词程序 - PyQt5 + JSON存储 + MySQL自动备份
功能：导入/导出单词、复习测试、统计查看、联网查词
"""
import sys
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QTableWidget, QTableWidgetItem,
    QTabWidget, QFileDialog, QMessageBox, QComboBox, QTextEdit,
    QHeaderView, QGroupBox, QProgressDialog, QAbstractItemView, QScrollArea
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt5.QtGui import QFont
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent

DATA_FILE = "words_data.json"
BACKUP_FLAG_FILE = "last_backup.txt"

# MySQL配置（根据你的实际情况修改）
MYSQL_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "123456",
    "database": "words_backup",
    "charset": "utf8mb4"
}


def backup_to_mysql(words_data):
    """备份数据到MySQL"""
    try:
        import pymysql
        
        # 先连接MySQL（不指定数据库），创建数据库
        try:
            conn = pymysql.connect(
                host=MYSQL_CONFIG["host"],
                user=MYSQL_CONFIG["user"],
                password=MYSQL_CONFIG["password"],
                charset=MYSQL_CONFIG["charset"]
            )
            cursor = conn.cursor()
            cursor.execute("CREATE DATABASE IF NOT EXISTS words_backup CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"[备份] 创建数据库时出错: {e}")
        
        # 连接到指定数据库
        conn = pymysql.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        
        # 创建表（如果不存在）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS words_backup (
                id INT AUTO_INCREMENT PRIMARY KEY,
                backup_date DATE NOT NULL,
                word VARCHAR(200) NOT NULL,
                meaning TEXT,
                examples JSON,
                review_count INT DEFAULT 0,
                last_review VARCHAR(50),
                last_review_date VARCHAR(20),
                today_reviewed BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_backup_date (backup_date),
                INDEX idx_word (word)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        
        # 获取今天日期
        today = datetime.now().strftime("%Y-%m-%d")
        
        # 删除今天的旧备份（如果有）
        cursor.execute("DELETE FROM words_backup WHERE backup_date = %s", (today,))
        
        # 插入新备份
        for word, data in words_data.items():
            cursor.execute("""
                INSERT INTO words_backup 
                (backup_date, word, meaning, examples, review_count, last_review, last_review_date, today_reviewed)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                today,
                word,
                data.get("meaning", ""),
                json.dumps(data.get("examples", []), ensure_ascii=False),
                data.get("review_count", 0),
                data.get("last_review", ""),
                data.get("last_review_date", ""),
                data.get("today_reviewed", False)
            ))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        # 记录备份时间
        with open(BACKUP_FLAG_FILE, 'w') as f:
            f.write(today)
        
        return True, f"成功备份 {len(words_data)} 个单词到MySQL"
    
    except ImportError:
        return False, "未安装pymysql库，跳过MySQL备份"
    except Exception as e:
        return False, f"MySQL备份失败: {str(e)}"


def should_backup_today():
    """检查今天是否需要备份"""
    if not os.path.exists(BACKUP_FLAG_FILE):
        return True
    
    try:
        with open(BACKUP_FLAG_FILE, 'r') as f:
            last_backup = f.read().strip()
        today = datetime.now().strftime("%Y-%m-%d")
        return last_backup != today
    except:
        return True


class FetchWorker(QThread):
    """后台获取单词含义的线程"""
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(int, list)  # 返回数量和单词列表
    
    def __init__(self, manager, words):
        super().__init__()
        self.manager = manager
        self.words = words
        self.cancelled = False
        self.added_words = []  # 存储添加的单词和含义
    
    def run(self):
        count = 0
        total = len(self.words)
        for i, word in enumerate(self.words):
            if self.cancelled:
                break
            self.progress.emit(i + 1, total, word)
            success, meaning = self.manager.add_word_auto(word)
            if success:
                count += 1
                self.added_words.append((word, meaning))
        self.finished.emit(count, self.added_words)


class WordManager:
    """单词数据管理"""
    def __init__(self):
        self.words = {}
        self.today_tasks = []  # 今日任务列表
        self.today_completed = set()  # 今日已完成
        self.load_data()
        self.init_today_tasks()
    
    def load_data(self):
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 兼容旧数据，添加last_review_date字段
                for word, info in data.items():
                    if "last_review_date" not in info:
                        # 从last_review提取日期
                        last_review = info.get("last_review", "")
                        if last_review and len(last_review) >= 10:
                            info["last_review_date"] = last_review[:10]  # 提取 YYYY-MM-DD
                        else:
                            info["last_review_date"] = ""
                    if "today_reviewed" not in info:
                        info["today_reviewed"] = False
                self.words = data
                self.save_data()  # 保存更新后的数据
    
    def save_data(self):
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.words, f, ensure_ascii=False, indent=2)
    
    def add_word(self, word, meaning, examples=None):
        word = word.strip()
        if word and word not in self.words:
            self.words[word] = {
                "meaning": meaning.strip(),
                "examples": examples if examples else [],
                "review_count": 0,
                "last_review": "",
                "last_review_date": "",
                "today_reviewed": False
            }
            self.save_data()
            return True, meaning
        return False, ""
    
    def review_word(self, word):
        """复习单词 - 复习次数+1，更新时间"""
        if word in self.words:
            self.words[word]["review_count"] += 1
            self.words[word]["last_review"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            self.words[word]["last_review_date"] = datetime.now().strftime("%Y-%m-%d")
            self.words[word]["today_reviewed"] = True
            if word not in self.today_completed:
                self.today_completed.add(word)
            self.save_data()
    
    def mark_reviewed_without_count(self, word):
        """标记为已复习但不增加复习次数 - 只更新时间"""
        if word in self.words:
            self.words[word]["last_review"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            self.words[word]["last_review_date"] = datetime.now().strftime("%Y-%m-%d")
            self.words[word]["today_reviewed"] = True
            if word not in self.today_completed:
                self.today_completed.add(word)
            self.save_data()
    
    def delete_word(self, word):
        if word in self.words:
            del self.words[word]
            self.save_data()
            return True
        return False
    
    def delete_words(self, words):
        count = 0
        for word in words:
            if word in self.words:
                del self.words[word]
                count += 1
        if count > 0:
            self.save_data()
        return count
    
    def get_words_to_review(self):
        """获取今日待复习的单词（从今日任务列表）"""
        return [w for w in self.today_tasks if w not in self.today_completed]
    
    def init_today_tasks(self):
        """初始化今日任务 - 基于艾宾浩斯遗忘曲线"""
        import random
        from datetime import datetime, timedelta
        
        today = datetime.now().date()
        today_str = today.strftime("%Y-%m-%d")
        day_rng = random.Random(f"daily-review-{today_str}")
        
        self.today_tasks = []
        self.today_completed = set()
        
        unreviewed = []  # 未复习的
        reviewing = []   # 复习中的（1-2次）
        mastered = []    # 已掌握的（>=3次）
        mastered_due = []  # 已掌握且到期的
        
        # 艾宾浩斯复习间隔（天数）
        ebbinghaus_intervals = {
            3: 2,   # 第3次复习：2天后
            4: 4,   # 第4次复习：4天后
            5: 7,   # 第5次复习：7天后
            6: 15,  # 第6次复习：15天后
        }
        default_interval = 30  # 第7次及以后：30天后
        
        for word, data in self.words.items():
            last_review_date = data.get("last_review_date", "")
            review_count = data.get("review_count", 0)
            
            if review_count == 0:
                # 未复习的单词：全部加入
                if last_review_date == today.strftime("%Y-%m-%d"):
                    self.today_completed.add(word)
                unreviewed.append(word)
            elif review_count < 3:
                # 复习中的单词（1-2次）：全部加入
                if last_review_date == today.strftime("%Y-%m-%d"):
                    self.today_completed.add(word)
                reviewing.append(word)
            else:
                # 已掌握的单词（>=3次）：根据艾宾浩斯曲线判断
                mastered.append(word)
                
                # 检查是否到期
                if last_review_date:
                    try:
                        last_date = datetime.strptime(last_review_date, "%Y-%m-%d").date()
                        days_since_review = (today - last_date).days
                        
                        # 获取应该的复习间隔
                        required_interval = ebbinghaus_intervals.get(review_count, default_interval)
                        
                        # 如果距离上次复习已经达到或超过间隔天数，加入到期列表
                        if days_since_review >= required_interval:
                            mastered_due.append(word)
                            # 如果今天已复习过，标记为已完成
                            if last_review_date == today.strftime("%Y-%m-%d"):
                                self.today_completed.add(word)
                    except:
                        # 如果日期解析失败，加入到期列表
                        mastered_due.append(word)
                else:
                    # 如果没有复习日期，加入到期列表
                    mastered_due.append(word)
        
        # 1. 未复习的单词：全部加入
        unreviewed.sort()
        reviewing.sort(key=lambda w: (self.words[w]["review_count"], w))
        mastered.sort(key=lambda w: (self.words[w]["review_count"], w))
        self.today_tasks.extend(unreviewed)
        
        # 2. 复习中的单词：全部加入
        self.today_tasks.extend(reviewing)
        
        # 检查当前任务数量，如果已经超过60个，不再添加已掌握的单词
        current_count = len(self.today_tasks)
        max_total = 60  # 每天最多60个
        
        if current_count < max_total:
            remaining_slots = max_total - current_count
            
            # 3. 已掌握的单词：优先加入到期的，不足10个则随机补充
            if mastered_due:
                # 按复习次数排序，复习次数少的优先
                mastered_due_sorted = sorted(mastered_due, key=lambda w: (self.words[w]["review_count"], w))
                # 限制数量不超过剩余槽位
                add_count = min(len(mastered_due_sorted), remaining_slots)
                self.today_tasks.extend(mastered_due_sorted[:add_count])
            
            # 如果已掌握的到期单词不足10个，随机补充
            added_mastered = min(len(mastered_due), remaining_slots)
            if added_mastered < 10 and added_mastered < remaining_slots and mastered:
                # 从未到期的已掌握单词中随机选择
                not_due = [w for w in mastered if w not in mastered_due]
                if not_due:
                    # 使用日期作为种子，保证每天固定
                    # 按复习次数排序，优先选择复习次数少的
                    not_due_sorted = sorted(not_due, key=lambda w: (self.words[w]["review_count"], w))
                    # 从前30%中随机选择
                    candidate_count = max(10, len(not_due_sorted) // 3)
                    candidates = not_due_sorted[:candidate_count]
                    
                    need_count = min(10 - added_mastered, remaining_slots - added_mastered)
                    sample_count = min(need_count, len(candidates))
                    sampled = day_rng.sample(candidates, sample_count)
                    self.today_tasks.extend(sampled)
                    
                    # 检查这些单词今天是否已复习
                    for word in sampled:
                        if self.words[word].get("last_review_date", "") == today.strftime("%Y-%m-%d"):
                            self.today_completed.add(word)
                    
        
        # 打乱顺序
        day_rng.shuffle(self.today_tasks)
    
    def get_mastered_words(self):
        return {w: d for w, d in self.words.items() if d["review_count"] >= 3}
    
    def get_unreviewed_words(self):
        return {w: d for w, d in self.words.items() if d["review_count"] == 0}

    def import_from_text(self, text):
        count = 0
        added_words = []
        skipped = 0
        for line in text.strip().split('\n'):
            if ',' in line:
                parts = line.split(',', 1)
                if len(parts) == 2 and parts[0].strip() != '单词':
                    word = parts[0].strip()
                    # 检查是否已存在（不区分大小写）
                    word_lower = word.lower()
                    exists = any(w.lower() == word_lower for w in self.words.keys())
                    if exists:
                        skipped += 1
                        continue
                    success, meaning = self.add_word(word, parts[1])
                    if success:
                        count += 1
                        added_words.append((word, meaning))
        return count, added_words, skipped
    
    def export_to_text(self):
        lines = ["单词,详细含义"]
        for word, data in self.words.items():
            lines.append(f"{word},{data['meaning']}")
        return '\n'.join(lines)
    
    def fetch_meaning(self, word):
        """联网获取单词含义和例句"""
        meaning = ""
        examples = []
        
        # 1. 使用有道词典jsonp接口获取完整释义和例句
        try:
            url = f"https://dict.youdao.com/jsonapi?q={urllib.parse.quote(word)}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                
                # 获取释义
                ec = data.get("ec", {})
                if ec:
                    word_list = ec.get("word", [])
                    if word_list:
                        trs = word_list[0].get("trs", [])
                        meanings = []
                        for tr in trs:
                            for t in tr.get("tr", []):
                                if t.get("l", {}).get("i"):
                                    meanings.append(t["l"]["i"][0])
                        if meanings:
                            meaning = "; ".join(meanings)
                
                # 获取例句（从blng双语例句，只保留英文）
                blng = data.get("blng", {})
                if blng:
                    blng_sents = blng.get("blng_sents_part", {}).get("sentence-pair", [])
                    for sent in blng_sents[:3]:  # 最多取3个例句
                        en = sent.get("sentence", "")
                        if en:
                            examples.append({"en": en, "cn": ""})
        except:
            pass
        
        # 2. 备用：有道suggest接口（只获取释义）
        if not meaning:
            try:
                url = f"https://dict.youdao.com/suggest?num=1&doctype=json&q={urllib.parse.quote(word)}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    if data.get("result", {}).get("code") == 200:
                        entries = data.get("data", {}).get("entries", [])
                        if entries and entries[0].get("explain"):
                            meaning = entries[0]["explain"]
            except:
                pass
        
        # 3. 备用：Free Dictionary API（英文释义和例句）
        if not examples:
            try:
                url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(word)}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    if isinstance(data, list) and data:
                        meanings_data = data[0].get("meanings", [])
                        # 获取释义（如果还没有）
                        if not meaning and meanings_data:
                            defs = meanings_data[0].get("definitions", [])
                            if defs:
                                meaning = defs[0].get("definition", "")
                        
                        # 获取例句
                        for meaning_item in meanings_data:
                            defs = meaning_item.get("definitions", [])
                            for d in defs:
                                example = d.get("example", "")
                                if example:
                                    examples.append({"en": example, "cn": ""})
                                    if len(examples) >= 3:
                                        break
                            if len(examples) >= 3:
                                break
            except:
                pass
        
        # 4. 备用：Tatoeba 例句库
        if not examples or len(examples) < 3:
            try:
                url = f"https://tatoeba.org/en/api_v0/search?from=eng&to=zho&query={urllib.parse.quote(word)}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    results = data.get("results", [])
                    for item in results[:5]:
                        eng_text = item.get("text", "")
                        if eng_text and len(eng_text) < 200:
                            examples.append({"en": eng_text, "cn": ""})
                            if len(examples) >= 3:
                                break
            except:
                pass
        
        # 5. 备用：Vocabulary.com
        if not examples:
            try:
                url = f"https://www.vocabulary.com/dictionary/{urllib.parse.quote(word.lower())}"
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                })
                with urllib.request.urlopen(req, timeout=5) as resp:
                    html = resp.read().decode('utf-8')
                    import re
                    # Vocabulary.com的例句模式
                    matches = re.findall(r'<h3 class="example">([^<]+)</h3>', html)
                    for match in matches[:3]:
                        sentence = match.strip()
                        if sentence and 10 < len(sentence) < 200:
                            examples.append({"en": sentence, "cn": ""})
                            if len(examples) >= 3:
                                break
            except:
                pass
        
        return meaning, examples
    
    def add_word_auto(self, word):
        word = word.strip()
        if not word or word in self.words:
            return False, ""
        
        meaning, examples = self.fetch_meaning(word)
        if not meaning:
            meaning = "(未找到释义)"
        
        self.words[word] = {
            "meaning": meaning,
            "examples": examples,
            "review_count": 0,
            "last_review": "",
            "last_review_date": "",
            "today_reviewed": False
        }
        self.save_data()
        return True, meaning

    def import_words_only(self, text):
        """只导入单词和词组 - 高鲁棒性提取"""
        import re
        words = set()
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        skip_words = {'单词', 'word', 'words', '词汇', '英语', 'english', 'vocabulary', 
                      '详细含义', '含义', '意思', 'meaning', 'definition'}
        
        for line in text.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            # 先尝试按逗号、分号等分隔
            parts = re.split(r'[,;，；\t、/|]+', line)
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                
                # 检查是否是词组（包含空格的英文）
                # 匹配：next of kin, take care of, etc.
                phrase_match = re.match(r'^[A-Za-z]+(?:\s+[A-Za-z]+)+$', part)
                if phrase_match:
                    phrase = part
                    phrase_lower = phrase.lower()
                    exists = any(w.lower() == phrase_lower for w in self.words.keys())
                    if (len(phrase) >= 3 and 
                        phrase.lower() not in skip_words and
                        not exists):
                        words.add(phrase)
                    continue
                
                # 否则按单词提取
                matches = re.findall(r"[A-Za-z]+(?:[-'][A-Za-z]+)*", part)
                for word in matches:
                    word = word.strip("'-")
                    word_lower = word.lower()
                    exists = any(w.lower() == word_lower for w in self.words.keys())
                    if (len(word) >= 2 and 
                        not word.isdigit() and 
                        word.lower() not in skip_words and
                        not exists):
                        words.add(word)
        return list(words)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.manager = WordManager()
        self.current_word = None
        self.fetch_thread = None
        self.media_player = QMediaPlayer()  # 音频播放器
        self.init_ui()
        
        # 检查是否需要备份到MySQL
        self.check_and_backup()
    
    def check_and_backup(self):
        """检查并执行每日备份"""
        if should_backup_today() and self.manager.words:
            success, message = backup_to_mysql(self.manager.words)
            # 写入日志文件
            log_file = "backup_log.txt"
            with open(log_file, 'a', encoding='utf-8') as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"[{timestamp}] {message}\n")
            
            # 弹窗提示
            if success:
                QMessageBox.information(self, "备份成功", message)
            else:
                QMessageBox.warning(self, "备份失败", message)
    
    def init_ui(self):
        self.setWindowTitle("背单词助手")
        self.resize(1200, 900)
        self.setMinimumSize(1000, 800)
        
        # 设置全局字体
        font = QFont("Microsoft YaHei", 11)
        self.setFont(font)
        
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        # 标签页
        tabs = QTabWidget()
        tabs.setFont(QFont("Microsoft YaHei", 12))
        tabs.addTab(self.create_review_tab(), "复习")
        tabs.addTab(self.create_import_tab(), "导入/导出")
        tabs.addTab(self.create_stats_tab(), "统计/管理")
        layout.addWidget(tabs)
        
        self.update_stats()

    def create_review_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(15)
        
        # 统计信息
        self.stats_label = QLabel()
        self.stats_label.setFont(QFont("Microsoft YaHei", 12))
        self.stats_label.setStyleSheet("padding: 10px; background: #e8f4fd; border-radius: 5px;")
        layout.addWidget(self.stats_label)
        
        # 单词显示区
        word_group = QGroupBox("当前单词")
        word_group.setFont(QFont("Microsoft YaHei", 11))
        word_layout = QVBoxLayout(word_group)
        word_layout.setContentsMargins(20, 20, 20, 20)
        
        # 单词和发音按钮的水平布局
        word_container = QHBoxLayout()
        word_container.addStretch()
        
        self.word_label = QLabel("点击「开始复习」开始")
        self.word_label.setFont(QFont("Microsoft YaHei", 28, QFont.Bold))
        self.word_label.setAlignment(Qt.AlignCenter)
        self.word_label.setMinimumHeight(120)  # 增加高度以容纳复习次数
        self.word_label.setWordWrap(True)  # 允许换行
        self.word_label.setTextFormat(Qt.RichText)  # 支持HTML格式
        word_container.addWidget(self.word_label)
        
        # 发音按钮（苹果风格）
        self.sound_btn = QPushButton("🔊")
        self.sound_btn.setFont(QFont("Segoe UI Emoji", 18))
        self.sound_btn.setFixedSize(44, 44)
        self.sound_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 122, 255, 0.1);
                border: 1px solid rgba(0, 122, 255, 0.3);
                border-radius: 22px;
                color: #007AFF;
            }
            QPushButton:hover {
                background-color: rgba(0, 122, 255, 0.2);
                border: 1px solid rgba(0, 122, 255, 0.5);
            }
            QPushButton:pressed {
                background-color: rgba(0, 122, 255, 0.3);
                border: 1px solid rgba(0, 122, 255, 0.7);
            }
        """)
        self.sound_btn.setCursor(Qt.PointingHandCursor)
        self.sound_btn.setToolTip("播放发音")
        self.sound_btn.clicked.connect(self.play_pronunciation)
        self.sound_btn.setVisible(False)
        word_container.addWidget(self.sound_btn)
        
        word_container.addStretch()
        word_layout.addLayout(word_container)
        layout.addWidget(word_group)
        
        # 开始复习按钮
        start_btn_layout = QHBoxLayout()
        self.start_review_btn = QPushButton("开始复习")
        self.start_review_btn.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        self.start_review_btn.setMinimumHeight(50)
        self.start_review_btn.setStyleSheet("background: #007bff; color: white; border: none; border-radius: 5px;")
        self.start_review_btn.clicked.connect(self.start_review)
        start_btn_layout.addWidget(self.start_review_btn)
        layout.addLayout(start_btn_layout)
        
        # 输入区
        input_layout = QHBoxLayout()
        input_label = QLabel("输入含义:")
        input_label.setFont(QFont("Microsoft YaHei", 11))
        input_layout.addWidget(input_label)
        self.answer_input = QLineEdit()
        self.answer_input.setFont(QFont("Microsoft YaHei", 12))
        self.answer_input.setMinimumHeight(35)
        self.answer_input.setPlaceholderText("输入单词的中文含义...")
        self.answer_input.returnPressed.connect(self.check_answer)
        input_layout.addWidget(self.answer_input)
        layout.addLayout(input_layout)
        
        # 结果显示
        self.result_label = QLabel()
        self.result_label.setFont(QFont("Microsoft YaHei", 11))
        self.result_label.setWordWrap(True)
        self.result_label.setMinimumHeight(80)
        self.result_label.setStyleSheet("padding: 10px; background: #f5f5f5; border-radius: 5px;")
        layout.addWidget(self.result_label)
        
        # 例句显示和编辑
        example_group = QGroupBox("例句")
        example_group.setFont(QFont("Microsoft YaHei", 11))
        example_layout = QVBoxLayout(example_group)
        
        # 使用 QTextEdit 替代 QLabel，支持编辑和复制
        self.example_text = QTextEdit()
        self.example_text.setFont(QFont("Microsoft YaHei", 10))
        self.example_text.setMinimumHeight(120)
        self.example_text.setMaximumHeight(200)
        self.example_text.setStyleSheet("padding: 10px; background: #fff; border-radius: 5px;")
        self.example_text.setReadOnly(False)  # 允许编辑
        example_layout.addWidget(self.example_text)
        
        # 保存例句按钮
        save_example_btn = QPushButton("保存例句修改")
        save_example_btn.setFont(QFont("Microsoft YaHei", 10))
        save_example_btn.setMinimumHeight(32)
        save_example_btn.setStyleSheet("background: #28a745; color: white; border: none; border-radius: 4px;")
        save_example_btn.clicked.connect(self.save_example_changes)
        example_layout.addWidget(save_example_btn)
        
        layout.addWidget(example_group)
        
        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(15)
        
        check_btn = QPushButton("检查答案")
        check_btn.setFont(QFont("Microsoft YaHei", 11))
        check_btn.setMinimumHeight(40)
        check_btn.setMinimumWidth(120)
        check_btn.setStyleSheet("background: #28a745; color: white; border: none; border-radius: 5px;")
        check_btn.clicked.connect(self.check_answer)
        btn_layout.addWidget(check_btn)
        
        show_answer_btn = QPushButton("显示答案")
        show_answer_btn.setFont(QFont("Microsoft YaHei", 11))
        show_answer_btn.setMinimumHeight(40)
        show_answer_btn.setMinimumWidth(120)
        show_answer_btn.setStyleSheet("background: #6c757d; color: white; border: none; border-radius: 5px;")
        show_answer_btn.clicked.connect(self.show_answer)
        btn_layout.addWidget(show_answer_btn)
        
        show_example_btn = QPushButton("显示例句")
        show_example_btn.setFont(QFont("Microsoft YaHei", 11))
        show_example_btn.setMinimumHeight(40)
        show_example_btn.setMinimumWidth(120)
        show_example_btn.setStyleSheet("background: #17a2b8; color: white; border: none; border-radius: 5px;")
        show_example_btn.clicked.connect(self.show_examples)
        btn_layout.addWidget(show_example_btn)
        
        layout.addLayout(btn_layout)
        
        # 第二行按钮：我会了/我还不会
        btn_layout2 = QHBoxLayout()
        btn_layout2.setSpacing(15)
        
        self.know_btn = QPushButton("✓ 我会了")
        self.know_btn.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        self.know_btn.setMinimumHeight(50)
        self.know_btn.setStyleSheet("background: #28a745; color: white; border: none; border-radius: 5px;")
        self.know_btn.clicked.connect(self.mark_as_known)
        self.know_btn.setVisible(False)  # 初始隐藏
        btn_layout2.addWidget(self.know_btn)
        
        self.dont_know_btn = QPushButton("✗ 我还不会")
        self.dont_know_btn.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        self.dont_know_btn.setMinimumHeight(50)
        self.dont_know_btn.setStyleSheet("background: #dc3545; color: white; border: none; border-radius: 5px;")
        self.dont_know_btn.clicked.connect(self.mark_as_unknown)
        self.dont_know_btn.setVisible(False)  # 初始隐藏
        btn_layout2.addWidget(self.dont_know_btn)
        
        layout.addLayout(btn_layout2)
        layout.addStretch()
        return widget

    def create_import_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)
        
        # 文本输入区
        tip_label = QLabel("支持多种格式：每行一个单词、空格/逗号分隔、或直接粘贴英文文章")
        tip_label.setFont(QFont("Microsoft YaHei", 10))
        tip_label.setStyleSheet("color: #666;")
        layout.addWidget(tip_label)
        
        self.import_text = QTextEdit()
        self.import_text.setFont(QFont("Microsoft YaHei", 11))
        self.import_text.setMinimumHeight(150)
        self.import_text.setPlaceholderText("例如:\napple banana orange\n\n或:\napple,苹果\nbanana,香蕉")
        layout.addWidget(self.import_text)
        
        # 导入按钮
        btn_layout1 = QHBoxLayout()
        btn_layout1.setSpacing(10)
        
        import_auto_btn = QPushButton("导入单词 (自动查词)")
        import_auto_btn.setFont(QFont("Microsoft YaHei", 11))
        import_auto_btn.setMinimumHeight(38)
        import_auto_btn.setStyleSheet("background: #007bff; color: white; border: none; border-radius: 5px;")
        import_auto_btn.clicked.connect(self.import_words_auto)
        btn_layout1.addWidget(import_auto_btn)
        
        import_text_btn = QPushButton("导入 (带含义)")
        import_text_btn.setFont(QFont("Microsoft YaHei", 11))
        import_text_btn.setMinimumHeight(38)
        import_text_btn.setStyleSheet("background: #6c757d; color: white; border: none; border-radius: 5px;")
        import_text_btn.clicked.connect(self.import_from_text)
        btn_layout1.addWidget(import_text_btn)
        layout.addLayout(btn_layout1)
        
        # 文件/剪贴板按钮
        btn_layout2 = QHBoxLayout()
        btn_layout2.setSpacing(10)
        
        import_file_btn = QPushButton("从文件导入")
        import_file_btn.setFont(QFont("Microsoft YaHei", 11))
        import_file_btn.setMinimumHeight(38)
        import_file_btn.setStyleSheet("background: #17a2b8; color: white; border: none; border-radius: 5px;")
        import_file_btn.clicked.connect(self.import_from_file_auto)
        btn_layout2.addWidget(import_file_btn)
        
        import_clip_btn = QPushButton("从剪贴板导入")
        import_clip_btn.setFont(QFont("Microsoft YaHei", 11))
        import_clip_btn.setMinimumHeight(38)
        import_clip_btn.setStyleSheet("background: #17a2b8; color: white; border: none; border-radius: 5px;")
        import_clip_btn.clicked.connect(self.import_from_clipboard_auto)
        btn_layout2.addWidget(import_clip_btn)
        
        export_btn = QPushButton("导出到文件")
        export_btn.setFont(QFont("Microsoft YaHei", 11))
        export_btn.setMinimumHeight(38)
        export_btn.setStyleSheet("background: #28a745; color: white; border: none; border-radius: 5px;")
        export_btn.clicked.connect(self.export_to_file)
        btn_layout2.addWidget(export_btn)
        
        layout.addLayout(btn_layout2)
        layout.addStretch()
        return widget
    
    def create_stats_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(10)
        
        # 筛选和操作栏
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        
        filter_label = QLabel("筛选:")
        filter_label.setFont(QFont("Microsoft YaHei", 11))
        toolbar.addWidget(filter_label)
        
        self.filter_combo = QComboBox()
        self.filter_combo.setFont(QFont("Microsoft YaHei", 11))
        self.filter_combo.setMinimumWidth(150)
        self.filter_combo.setMinimumHeight(32)
        self.filter_combo.addItems(["全部单词", "未复习", "复习中(1-2次)", "已掌握(>=3次)"])
        self.filter_combo.currentIndexChanged.connect(self.update_table)
        toolbar.addWidget(self.filter_combo)
        
        refresh_btn = QPushButton("刷新")
        refresh_btn.setFont(QFont("Microsoft YaHei", 11))
        refresh_btn.setMinimumHeight(32)
        refresh_btn.setStyleSheet("background: #007bff; color: white; border: none; border-radius: 4px; padding: 5px 15px;")
        refresh_btn.clicked.connect(self.update_table)
        toolbar.addWidget(refresh_btn)
        
        toolbar.addStretch()
        
        delete_btn = QPushButton("删除选中")
        delete_btn.setFont(QFont("Microsoft YaHei", 11))
        delete_btn.setMinimumHeight(32)
        delete_btn.setStyleSheet("background: #dc3545; color: white; border: none; border-radius: 4px; padding: 5px 15px;")
        delete_btn.clicked.connect(self.delete_selected_words)
        toolbar.addWidget(delete_btn)
        
        delete_all_btn = QPushButton("清空全部")
        delete_all_btn.setFont(QFont("Microsoft YaHei", 11))
        delete_all_btn.setMinimumHeight(32)
        delete_all_btn.setStyleSheet("background: #dc3545; color: white; border: none; border-radius: 4px; padding: 5px 15px;")
        delete_all_btn.clicked.connect(self.delete_all_words)
        toolbar.addWidget(delete_all_btn)
        
        save_btn = QPushButton("保存修改")
        save_btn.setFont(QFont("Microsoft YaHei", 11))
        save_btn.setMinimumHeight(32)
        save_btn.setStyleSheet("background: #28a745; color: white; border: none; border-radius: 4px; padding: 5px 15px;")
        save_btn.clicked.connect(self.save_table_changes)
        toolbar.addWidget(save_btn)
        
        layout.addLayout(toolbar)
        
        # 表格
        self.table = QTableWidget()
        self.table.setFont(QFont("Microsoft YaHei", 10))
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["单词", "含义", "例句", "复习次数", "上次复习"])
        self.table.horizontalHeader().setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(60)
        self.table.setWordWrap(True)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked)  # 双击编辑
        self.table.setSortingEnabled(True)  # 启用排序
        self.table.itemChanged.connect(self.on_table_item_changed)  # 监听修改
        layout.addWidget(self.table)
        
        # 统计信息
        self.table_stats = QLabel()
        self.table_stats.setFont(QFont("Microsoft YaHei", 10))
        self.table_stats.setStyleSheet("color: #666; padding: 5px;")
        layout.addWidget(self.table_stats)
        
        return widget

    def update_stats(self):
        total = len(self.manager.words)
        unreviewed = len(self.manager.get_unreviewed_words())
        mastered = len(self.manager.get_mastered_words())
        reviewing = total - unreviewed - mastered
        
        # 今日任务进度
        today_total = len(self.manager.today_tasks)
        today_done = len(self.manager.today_completed)
        
        self.stats_label.setText(
            f"总计: {total}  |  未复习: {unreviewed}  |  复习中: {reviewing}  |  已掌握: {mastered}  |  "
            f"今日任务: {today_done}/{today_total}"
        )
    
    def next_word(self):
        words_to_review = self.manager.get_words_to_review()
        if not words_to_review:
            self.word_label.setText("今日任务已完成！🎉")
            self.current_word = None
            self.example_text.clear()
            self.know_btn.setVisible(False)
            self.dont_know_btn.setVisible(False)
            self.sound_btn.setVisible(False)  # 隐藏发音按钮
            return
        
        self.current_word = words_to_review[0]
        
        # 获取复习次数
        review_count = self.manager.words[self.current_word]["review_count"]
        
        # 使用HTML格式显示单词和复习次数
        word_html = f'''
        <div style="position: relative; text-align: center;">
            <div style="font-size: 28pt; font-weight: bold;">{self.current_word}</div>
            <div style="font-size: 10pt; color: #888; margin-top: 5px;">(已复习 {review_count} 次)</div>
        </div>
        '''
        self.word_label.setText(word_html)
        self.sound_btn.setVisible(True)  # 显示发音按钮
        
        self.answer_input.clear()
        self.result_label.clear()
        self.example_text.clear()
        self.know_btn.setVisible(False)
        self.dont_know_btn.setVisible(False)
        self.answer_input.setFocus()
    
    def play_pronunciation(self):
        """播放单词发音"""
        if not self.current_word:
            return
        
        # 使用有道词典语音API
        # type=1: 美式发音, type=2: 英式发音
        url = f"https://dict.youdao.com/dictvoice?audio={urllib.parse.quote(self.current_word)}&type=1"
        self.media_player.setMedia(QMediaContent(QUrl(url)))
        self.media_player.play()
    
    def check_answer(self):
        if not self.current_word:
            QMessageBox.information(self, "提示", "请先点击「下一个」获取单词")
            return
        
        user_answer = self.answer_input.text().strip()
        if not user_answer:
            return
        
        word_data = self.manager.words[self.current_word]
        correct_meaning = word_data["meaning"]
        is_correct = self.fuzzy_match(user_answer, correct_meaning)
        
        if is_correct:
            count = self.manager.words[self.current_word]["review_count"]
            status = "已掌握！" if count >= 3 else f"(已复习{count}次)"
            self.result_label.setText(f"正确！{status}\n完整含义: {correct_meaning}")
            self.result_label.setStyleSheet("padding: 10px; background: #d4edda; border-radius: 5px; color: #155724;")
        else:
            self.result_label.setText(f"不太对\n正确含义: {correct_meaning}")
            self.result_label.setStyleSheet("padding: 10px; background: #f8d7da; border-radius: 5px; color: #721c24;")
        
        # 显示例句
        self.show_examples()
        
        # 显示"我会了"/"我还不会"按钮
        self.know_btn.setVisible(True)
        self.dont_know_btn.setVisible(True)
    
    def mark_as_known(self):
        """标记为已掌握 - 复习次数+1"""
        if not self.current_word:
            return
        
        self.manager.review_word(self.current_word)  # 复习次数+1，更新时间
        self.update_stats()
        
        # 隐藏按钮，进入下一个单词
        self.know_btn.setVisible(False)
        self.dont_know_btn.setVisible(False)
        self.next_word()
    
    def mark_as_unknown(self):
        """标记为不会 - 只更新时间，不增加复习次数"""
        if not self.current_word:
            return
        
        self.manager.mark_reviewed_without_count(self.current_word)  # 只更新时间
        self.update_stats()
        
        # 隐藏按钮，进入下一个单词
        self.know_btn.setVisible(False)
        self.dont_know_btn.setVisible(False)
        self.next_word()
    
    def start_review(self):
        """开始复习"""
        self.start_review_btn.setVisible(False)
        self.next_word()
    
    def fuzzy_match(self, user_input, correct):
        user_input = user_input.lower().replace(" ", "")
        correct_lower = correct.lower().replace(" ", "")
        
        if user_input in correct_lower or correct_lower in user_input:
            return True
        
        keywords = correct.split('；')[0].split('，')[0].split('、')[0].split(';')[0]
        keywords = keywords.replace(" ", "").lower()
        if user_input in keywords or keywords in user_input:
            return True
        
        if len(user_input) >= 2:
            for i in range(len(correct_lower) - 1):
                if correct_lower[i:i+2] in user_input:
                    return True
        
        return False
    
    def show_answer(self):
        if self.current_word:
            meaning = self.manager.words[self.current_word]["meaning"]
            self.result_label.setText(f"答案: {meaning}")
            self.result_label.setStyleSheet("padding: 10px; background: #cce5ff; border-radius: 5px; color: #004085;")
            # 显示例句
            self.show_examples()
            # 显示"我会了"/"我还不会"按钮
            self.know_btn.setVisible(True)
            self.dont_know_btn.setVisible(True)
    
    def show_examples(self):
        """显示当前单词的例句"""
        if not self.current_word:
            return
        
        word_data = self.manager.words[self.current_word]
        examples = word_data.get("examples", [])
        
        if not examples:
            self.example_text.setPlainText("暂无例句")
            return
        
        example_text = ""
        for i, ex in enumerate(examples, 1):  # 显示所有例句
            en = ex.get("en", "")
            if en:
                example_text += f"{i}. {en}\n\n"
        
        self.example_text.setPlainText(example_text.strip() if example_text else "暂无例句")
    
    def save_example_changes(self):
        """保存例句修改"""
        if not self.current_word:
            QMessageBox.warning(self, "提示", "当前没有正在复习的单词")
            return
        
        # 获取编辑后的文本
        text = self.example_text.toPlainText().strip()
        
        if not text or text == "暂无例句":
            # 清空例句
            self.manager.words[self.current_word]["examples"] = []
        else:
            # 解析例句（每行一个，忽略序号）
            examples = []
            for line in text.split('\n'):
                line = line.strip()
                if not line:
                    continue
                # 去掉开头的序号（如 "1. "）
                import re
                line = re.sub(r'^\d+\.\s*', '', line)
                if line:
                    examples.append({"en": line, "cn": ""})
            
            self.manager.words[self.current_word]["examples"] = examples
        
        self.manager.save_data()
        QMessageBox.information(self, "保存成功", f"已保存 {self.current_word} 的例句修改")
        self.update_table()
    
    def import_from_text(self):
        text = self.import_text.toPlainText()
        if text.strip():
            count, added_words, skipped = self.manager.import_from_text(text)
            msg = f"成功导入 {count} 个新单词"
            if skipped > 0:
                msg += f"，跳过 {skipped} 个已存在的单词"
            if added_words:
                # 显示添加的单词和解释
                msg += "：\n\n"
                for word, meaning in added_words[:10]:  # 最多显示10个
                    msg += f"{word}: {meaning[:50]}{'...' if len(meaning) > 50 else ''}\n"
                if len(added_words) > 10:
                    msg += f"\n... 还有 {len(added_words) - 10} 个单词"
            QMessageBox.information(self, "导入完成", msg)
            self.import_text.clear()
            self.manager.init_today_tasks()  # 重新初始化今日任务
            self.update_stats()
            self.update_table()
    
    def import_words_auto(self):
        text = self.import_text.toPlainText()
        if not text.strip():
            return
        words = self.manager.import_words_only(text)
        if not words:
            QMessageBox.information(self, "提示", "没有新单词需要导入（可能都已存在）")
            return
        self._start_fetch(words)
        self.import_text.clear()
    
    def import_from_file_auto(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", "文本文件 (*.txt *.csv)")
        if path:
            with open(path, 'r', encoding='utf-8') as f:
                words = self.manager.import_words_only(f.read())
            if words:
                self._start_fetch(words)
            else:
                QMessageBox.information(self, "提示", "没有新单词需要导入（可能都已存在）")
    
    def import_from_clipboard_auto(self):
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        if not text.strip():
            QMessageBox.warning(self, "提示", "剪贴板为空")
            return
        words = self.manager.import_words_only(text)
        if words:
            self._start_fetch(words)
        else:
            QMessageBox.information(self, "提示", "没有新单词需要导入（可能都已存在）")
    
    def _start_fetch(self, words):
        self.progress_dialog = QProgressDialog("正在获取单词含义...", "取消", 0, len(words), self)
        self.progress_dialog.setWindowTitle("导入中")
        self.progress_dialog.setMinimumWidth(350)
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        
        self.fetch_thread = FetchWorker(self.manager, words)
        self.fetch_thread.progress.connect(self._on_fetch_progress)
        self.fetch_thread.finished.connect(self._on_fetch_finished)
        self.progress_dialog.canceled.connect(self._on_fetch_cancel)
        self.fetch_thread.start()
    
    def _on_fetch_progress(self, current, total, word):
        self.progress_dialog.setValue(current)
        self.progress_dialog.setLabelText(f"正在查询: {word} ({current}/{total})")
    
    def _on_fetch_finished(self, count, added_words):
        self.progress_dialog.close()
        
        msg = f"成功导入 {count} 个新单词"
        if added_words:
            msg += "：\n\n"
            for word, meaning in added_words[:10]:  # 最多显示10个
                msg += f"{word}: {meaning[:50]}{'...' if len(meaning) > 50 else ''}\n"
            if len(added_words) > 10:
                msg += f"\n... 还有 {len(added_words) - 10} 个单词"
        
        QMessageBox.information(self, "导入完成", msg)
        self.manager.init_today_tasks()  # 重新初始化今日任务
        self.update_stats()
        self.update_table()
    
    def _on_fetch_cancel(self):
        if self.fetch_thread:
            self.fetch_thread.cancelled = True
    
    def export_to_file(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存文件", "words_export.txt", "文本文件 (*.txt)")
        if path:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(self.manager.export_to_text())
            QMessageBox.information(self, "导出完成", f"已导出到 {path}")
    
    def update_table(self):
        filter_idx = self.filter_combo.currentIndex()
        
        if filter_idx == 0:
            words = self.manager.words
        elif filter_idx == 1:
            words = self.manager.get_unreviewed_words()
        elif filter_idx == 2:
            words = {w: d for w, d in self.manager.words.items() if 0 < d["review_count"] < 3}
        else:
            words = self.manager.get_mastered_words()
        
        # 暂时断开信号和排序，避免触发itemChanged
        try:
            self.table.itemChanged.disconnect(self.on_table_item_changed)
        except:
            pass  # 如果信号未连接，忽略错误
        
        self.table.setSortingEnabled(False)  # 填充数据时禁用排序
        
        self.table.setRowCount(len(words))
        for row, (word, data) in enumerate(words.items()):
            word_item = QTableWidgetItem(word)
            word_item.setData(Qt.UserRole, word)
            word_item.setFlags(word_item.flags() & ~Qt.ItemIsEditable)  # 单词列不可编辑
            self.table.setItem(row, 0, word_item)
            
            meaning_item = QTableWidgetItem(data["meaning"])
            self.table.setItem(row, 1, meaning_item)
            
            # 例句列 - 显示完整例句，用换行分隔
            examples = data.get("examples", [])
            if examples:
                example_text = "\n\n".join([ex.get("en", "") for ex in examples])
                example_item = QTableWidgetItem(example_text)
            else:
                example_item = QTableWidgetItem("")
            self.table.setItem(row, 2, example_item)
            
            # 复习次数 - 使用数字类型以便正确排序
            count_item = QTableWidgetItem()
            count_item.setData(Qt.DisplayRole, data["review_count"])  # 使用数字
            count_item.setTextAlignment(Qt.AlignCenter)
            count_item.setFlags(count_item.flags() & ~Qt.ItemIsEditable)  # 复习次数不可编辑
            self.table.setItem(row, 3, count_item)
            
            time_item = QTableWidgetItem(data["last_review"] or "-")
            time_item.setTextAlignment(Qt.AlignCenter)
            time_item.setFlags(time_item.flags() & ~Qt.ItemIsEditable)  # 时间不可编辑
            self.table.setItem(row, 4, time_item)
        
        self.table.setSortingEnabled(True)  # 填充完成后启用排序
        
        # 重新连接信号
        try:
            self.table.itemChanged.connect(self.on_table_item_changed)
        except:
            pass  # 如果已经连接，忽略错误
        
        self.table_stats.setText(f"当前显示 {len(words)} 个单词")
    
    def on_table_item_changed(self, item):
        """表格单元格被修改时标记"""
        # 标记该行已修改（改变背景色）
        row = item.row()
        for col in range(self.table.columnCount()):
            cell_item = self.table.item(row, col)
            if cell_item:
                cell_item.setBackground(Qt.yellow)
    
    def save_table_changes(self):
        """保存表格的修改"""
        modified_count = 0
        
        # 暂时断开信号
        try:
            self.table.itemChanged.disconnect(self.on_table_item_changed)
        except:
            pass
        
        for row in range(self.table.rowCount()):
            word_item = self.table.item(row, 0)
            if not word_item:
                continue
            
            word = word_item.data(Qt.UserRole)
            if word not in self.manager.words:
                continue
            
            # 检查是否有修改（背景色为黄色）
            if word_item.background().color() != Qt.yellow:
                continue
            
            # 获取修改后的数据
            meaning_item = self.table.item(row, 1)
            example_item = self.table.item(row, 2)
            
            if meaning_item:
                self.manager.words[word]["meaning"] = meaning_item.text()
            
            if example_item:
                # 解析例句（每行一个例句）
                example_text = example_item.text().strip()
                if example_text:
                    examples = []
                    for line in example_text.split("\n"):
                        line = line.strip()
                        if line:
                            examples.append({"en": line, "cn": ""})
                    self.manager.words[word]["examples"] = examples
                else:
                    self.manager.words[word]["examples"] = []
            
            # 恢复背景色
            for col in range(self.table.columnCount()):
                cell_item = self.table.item(row, col)
                if cell_item:
                    cell_item.setBackground(Qt.white)
            
            modified_count += 1
        
        if modified_count > 0:
            self.manager.save_data()
            QMessageBox.information(self, "保存成功", f"已保存 {modified_count} 个单词的修改")
        else:
            QMessageBox.information(self, "提示", "没有需要保存的修改")
        
        # 重新连接信号
        try:
            self.table.itemChanged.connect(self.on_table_item_changed)
        except:
            pass
    
    def delete_selected_words(self):
        selected_rows = set(item.row() for item in self.table.selectedItems())
        if not selected_rows:
            QMessageBox.information(self, "提示", "请先选择要删除的单词")
            return
        
        words_to_delete = []
        for row in selected_rows:
            word_item = self.table.item(row, 0)
            if word_item:
                words_to_delete.append(word_item.data(Qt.UserRole))
        
        reply = QMessageBox.question(
            self, "确认删除", 
            f"确定要删除选中的 {len(words_to_delete)} 个单词吗？",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            count = self.manager.delete_words(words_to_delete)
            QMessageBox.information(self, "删除完成", f"已删除 {count} 个单词")
            self.manager.init_today_tasks()  # 重新初始化今日任务
            self.update_stats()
            self.update_table()
    
    def delete_all_words(self):
        if not self.manager.words:
            QMessageBox.information(self, "提示", "没有单词可删除")
            return
        
        reply = QMessageBox.warning(
            self, "确认清空", 
            f"确定要删除全部 {len(self.manager.words)} 个单词吗？\n此操作不可恢复！",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.manager.words.clear()
            self.manager.save_data()
            self.manager.init_today_tasks()  # 重新初始化今日任务
            QMessageBox.information(self, "清空完成", "已删除全部单词")
            self.update_stats()
            self.update_table()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
