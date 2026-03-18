from PyQt6 import QtWidgets, QtGui
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog,
    QTableWidget, QTableWidgetItem, QMessageBox, QCheckBox, QScrollArea,
    QToolBar, QProgressDialog, QApplication, QComboBox
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon, QColor, QAction
from xml.etree import ElementTree as ET
from datetime import datetime, timedelta
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
from collections import Counter
import csv
from PyQt6.QtWidgets import QToolTip
from PyQt6.QtWebEngineWidgets import QWebEngineView
import plotly.graph_objects as go
import os
import xlsxwriter
from collections import defaultdict
from PyPDF2 import PdfMerger
import re
from matplotlib.ticker import MaxNLocator
import platform
from PyQt6.QtWidgets import QLineEdit, QTextEdit, QFrame
import json
import requests
from openai import OpenAI
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QProgressBar
from PyQt6.QtWidgets import QTabWidget


def format_seconds(seconds: float) -> str:
    total_seconds = int(seconds)
    ms = int((seconds - total_seconds) * 1000)
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02}:{m:02}:{s:02}.{ms:03}"


class AIWorker(QThread):
    result_ready = pyqtSignal(str)

    def __init__(self, prompt, api_key, provider):
        super().__init__()
        self.prompt = prompt
        self.api_key = api_key
        self.provider = provider

    def run(self):
        try:
            if self.provider == "OpenAI":
                client = OpenAI(api_key=self.api_key)

                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "user", "content": self.prompt}
                    ],
                    timeout=300
                )

                text = response.choices[0].message.content

            elif self.provider == "Deepseek":
                client = OpenAI(api_key=self.api_key, base_url="https://api.deepseek.com")

                response = client.chat.completions.create(
                    model="deepseek-coder",
                    max_tokens=4000,
                    messages=[
                        {"role": "user", "content": self.prompt}
                    ],
                    timeout=300
                )

                text = response.choices[0].message.content

            elif self.provider == "Ollama (local)":
                r = requests.post(
                    "http://localhost:11434/api/generate",
                    json={
                        "model": "llama3",
                        "prompt": self.prompt,
                        "stream": False,
                        "options": {
                            "num_predict": 600
                        }
                    },
                    timeout=300
                )

                r.raise_for_status()

                data = r.json()

                text = data.get("response", "Ollama returned empty response.")

            else:
                text = "Nepodržan AI provider."

        except Exception as e:
            text = f"AI greška:\n{e}"

        self.result_ready.emit(text)


class AIAnalysisDialog(QDialog):
    AI_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "ai_config.json")

    def __init__(self, failed_tests, total_tests, parent=None):
        super().__init__(parent)

        self.ai_stage = "analysis"
        self.failure_groups = {}
        self.unique_failures = []
        self.current_batch = 0
        self.total_batches = 0
        self.batch_size = 2
        self.worker = None
        self.active_workers = []
        self.ai_results = []
        self.failed_tests = failed_tests
        self.total_tests = total_tests
        self.api_key = None

        self.setWindowTitle("AI analiza testova")
        self.resize(700, 500)

        layout = QVBoxLayout(self)

        provider_label = QLabel("AI provider")

        self.ai_combo = QComboBox()
        self.ai_combo.addItems([
            "Deepseek",
            "OpenAI",
            "Ollama (local)"
        ])
        self.ai_combo.currentTextChanged.connect(self.update_ai_info)
        self.ai_combo.currentTextChanged.connect(lambda: self.connect_button.setEnabled(True))
        layout.addWidget(provider_label)
        layout.addWidget(self.ai_combo)

        self.ai_info_label = QLabel("")
        self.ai_info_label.setStyleSheet("color: gray;")
        layout.addWidget(self.ai_info_label)

        pw_label = QLabel("Playwright verzija")

        self.pw_version_input = QLineEdit()
        self.pw_version_input.setPlaceholderText("npr. 1.43")

        layout.addWidget(pw_label)
        layout.addWidget(self.pw_version_input)

        # API key
        label = QLabel("AI ključ")
        self.api_input = QLineEdit()
        self.api_input.setPlaceholderText("Upiši AI API ključ")

        layout.addWidget(label)
        layout.addWidget(self.api_input)

        # connect button
        self.connect_button = QPushButton("Poveži")
        self.connect_button.clicked.connect(self.connect_with_ai)
        layout.addWidget(self.connect_button)

        stored_key, stored_provider, stored_pw = self.load_ai_settings()
        self.pw_version_input.setText(stored_pw)
            
        self.api_input.setText(stored_key)
        # if stored_key:
        #     self.connect_button.setEnabled(False)

        index = self.ai_combo.findText(stored_provider)
        if index >= 0:
            self.ai_combo.setCurrentIndex(index)

        # delimiter
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setMinimum(0)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.result_tabs = QTabWidget()

        self.analysis_box = QTextEdit()
        self.analysis_box.setReadOnly(True)

        self.grouping_box = QTextEdit()
        self.grouping_box.setReadOnly(True)

        self.summary_box = QTextEdit()
        self.summary_box.setReadOnly(True)

        self.bugreport_box = QTextEdit()
        self.bugreport_box.setReadOnly(True)

        self.flaky_box = QTextEdit()
        self.flaky_box.setReadOnly(True)

        self.fix_box = QTextEdit()
        self.fix_box.setReadOnly(True)

        self.qa_box = QTextEdit()
        self.qa_box.setReadOnly(True)

        self.result_tabs.addTab(self.analysis_box, "Analysis")
        self.result_tabs.addTab(self.grouping_box, "Grouping")
        self.result_tabs.addTab(self.summary_box, "Summary")
        self.result_tabs.addTab(self.bugreport_box, "Bug Reports")
        self.result_tabs.addTab(self.flaky_box, "Flaky Tests")
        self.result_tabs.addTab(self.fix_box, "Fix Suggestions")
        self.result_tabs.addTab(self.qa_box, "QA Insights")

        layout.addWidget(self.result_tabs)

        # send query button
        self.send_button = QPushButton("Pošalji upit")
        self.send_button.clicked.connect(self.send_queries)
        layout.addWidget(self.send_button)

        self.export_button = QPushButton("Export bug reports")
        self.export_button.clicked.connect(self.export_bug_reports)
        layout.addWidget(self.export_button)
        self.export_button.setEnabled(False)

        self.update_ai_info()

    def build_run_stats(self):
        total = len(self.failed_tests)

        suites = {}
        slow_tests = []

        for t in self.failed_tests:

            suite = t.get("suite", "unknown")
            time = t.get("time", 0)

            suites[suite] = suites.get(suite, 0) + 1

            if time > 10:
                slow_tests.append(t["name"])

        suite_summary = "\n".join(
            [f"{k}: {v} failures" for k, v in suites.items()]
        )

        slow_summary = "\n".join(slow_tests[:10])

        return f"""
    Total failed tests: {total}

    Failures per suite:
    {suite_summary}

    Slow tests (>10s):
    {slow_summary}
    """

    def build_qa_prompt(self):
        stats = self.build_run_stats()

        total_tests = self.total_tests
        failed = len(self.failed_tests)
        passed = total_tests - failed

        failure_rate = 0
        if total_tests > 0:
            failure_rate = (failed / total_tests) * 100

        pw_version = self.pw_version_input.text().strip()

        if not pw_version:
            pw_version = "unknown"

        prompt = f"""
    You are a senior QA automation architect.

    Playwright version used in the tests: {pw_version}
    Test framework: Playwright
    Language: JavaScript / TypeScript

    Below is a summary of a test run.

    Analyze the test run and provide:

    - key problems in the test suite
    - possible root causes
    - test architecture improvements
    - CI stability recommendations

    Test run statistics:

    Total tests: {total_tests}
    Passed tests: {passed}
    Failed tests: {failed}
    Failure rate: {failure_rate:.2f}%

    Interpretation rules:
    - A failure rate below 1% usually indicates a stable test suite.
    - Do NOT treat a very small number of failures as a major issue.
    - Focus on practical triage recommendations.
    - Provide realistic QA insights instead of dramatic conclusions.

    {stats}
    """

        return self.limit_prompt(prompt)

    def run_qa_analysis(self):
        self.ai_stage = "qa"
        print("run_qa_analysis started")
        self.status_label.setText("Generating QA insights...")

        prompt = self.build_qa_prompt()

        self.start_ai_task(prompt, self.handle_qa_result)

    def handle_qa_result(self, text):
        self.qa_box.append(text)

        self.status_label.setText("AI analiza završena.")
        print("QA RESULT RECEIVED")

    def build_fix_prompt(self):
        failures = ""

        pw_version = self.pw_version_input.text().strip()

        if not pw_version:
            pw_version = "unknown"

        context = self.load_error_context()

        for test in self.failed_tests:

            code = self.get_test_code_snippet(test)

            failures += f"""
    Test name:
    {test.get("name", "")}

    Failure log:
    {test.get("failure_details", "")}

    Test code snippet:
    {code}

    """

        prompt = f"""
    You are a senior Playwright QA automation engineer.

    Playwright version used in the tests: {pw_version}
    Test framework: Playwright
    Language: JavaScript / TypeScript

    Playwright error context:
    {context}

    Analysis rules:
    - Do NOT suggest increasing timeouts as the primary solution.
    - First identify the real root cause of the failure.
    - Timeout increases should only be suggested if the failure is clearly caused by slow async loading.
    - Prefer fixing locators, waits, or assertions instead of increasing timeouts.

    Playwright locator best practices:
    - Avoid locators that match multiple elements.
    - Prefer stable locators such as data-testid or unique attributes.
    - Avoid relying only on visible text when the UI is dynamic.
    - If getByRole or getByText resolves multiple elements, suggest a more specific locator.
    - Prefer deterministic selectors instead of generic ones.

    Below are failed automated tests.

    For each test suggest a concrete fix for the test code.

    Focus on:
    - locator issues
    - timing problems
    - incorrect assertions

    Return Playwright code example.

    Return:

    Test name
    Problem
    Suggested code fix

    Failures:

    {failures}
    """
        return self.limit_prompt(prompt)

    def run_fix_suggestions(self):
        if self.ai_stage != "flaky":
            return

        self.status_label.setText("Generating test fix suggestions...")

        prompt = self.build_fix_prompt()

        self.start_ai_task(prompt, self.handle_fix_result)

    def handle_fix_result(self, text):
        self.fix_box.append(text)

        self.ai_stage = "done"

        # self.status_label.setText("AI analiza završena.")

        self.send_button.setEnabled(True)

        self.run_qa_analysis()

    def export_bug_reports(self):
        text = self.bugreport_box.toPlainText().strip()

        if not text:
            QMessageBox.information(self, "Info", "Nema bug reportova za export.")
            return

        default_name = f"bug_reports_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.md"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Spremi bug reportove",
            default_name,
            "Markdown (*.md)"
        )

        if not file_path:
            return

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("# AI Generated Bug Reports\n\n")
                f.write(text)

            QMessageBox.information(self, "Export", "Bug reportovi uspješno spremljeni.")

        except Exception as e:
            QMessageBox.critical(self, "Greška", str(e))

    def connect_with_ai(self):
        key = self.api_input.text().strip()
        provider = self.ai_combo.currentText()

        if provider == "OpenAI" and not key:
            QMessageBox.warning(self, "Greška", "Upiši API ključ.")
            return

        self.api_key = key if provider != "Ollama (local)" else None

        self.save_ai_settings(key, provider)
        QMessageBox.information(self, "AI", "Povezano i spremljeno.")
        self.connect_button.setEnabled(False)

    def append_result(self, text):
        self.analysis_box.append(text)

        self.analysis_box.moveCursor(QtGui.QTextCursor.MoveOperation.End)

    def send_queries(self):
        if not self.failed_tests:
            QMessageBox.information(self, "Info", "Nema failed testova.")
            return

        self.ai_stage = "analysis"
        self.build_failure_groups()

        self.append_result(f"Detected {len(self.unique_failures)} unique failures from {len(self.failed_tests)} tests.")

        self.provider = self.ai_combo.currentText()

        self.append_result(f"Using AI provider: {self.provider}")

        self.batches = self.get_test_batches()

        self.total_batches = len(self.batches)
        self.current_batch = 0

        self.progress.setMaximum(self.total_batches)
        self.progress.setValue(0)

        self.ai_results = []

        self.send_button.setEnabled(False)

        self.run_next_batch()

    def load_ai_settings(self):
        if not os.path.exists(self.AI_CONFIG_FILE):
            return "", "OpenAI", ""

        try:
            with open(self.AI_CONFIG_FILE, "r") as f:
                data = json.load(f)

            provider = data.get("provider", "OpenAI")
            key = data.get(provider, "")
            pw = data.get("playwright_version", "")

            return key, provider, pw

        except:
            return "", "OpenAI", ""

    def save_ai_settings(self, key, provider):
        data = {}

        if os.path.exists(self.AI_CONFIG_FILE):
            with open(self.AI_CONFIG_FILE, "r") as f:
                try:
                    data = json.load(f)
                except:
                    data = {}

        data[provider] = key or ""
        data["provider"] = provider
        data["playwright_version"] = self.pw_version_input.text().strip()

        with open(self.AI_CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=2)

    def build_prompt(self, test):
        name = test.get("name", "")
        suite = test.get("suite", "")
        details = test.get("failure_details", "")[:800]

        prompt = f"""
    You are an expert QA automation engineer.

    Analyze the following FAILED automated test.

    Test name:
    {name}

    Test suite:
    {suite}

    Failure log:
    {details}

    Tasks:
    1. Explain the most likely reason for failure.
    2. Identify if this is:
    - test bug
    - locator issue
    - application bug
    - timing issue

    3. Suggest how to fix the test.

    Answer clearly.
    """
        return self.limit_prompt(prompt)
        # return prompt

    def build_grouping_prompt(self):
        joined = "\n\n".join(self.ai_results[:10])

        pw_version = self.pw_version_input.text().strip()

        if not pw_version:
            pw_version = "unknown"
            
        prompt = f"""
    You are a QA automation expert.

    Playwright version used in the tests: {pw_version}
    Test framework: Playwright
    Language: JavaScript / TypeScript

    Below are AI analyses of failed automated tests.

    Your task is to GROUP failures by root cause.

    IMPORTANT RULES:
    - Do NOT write long explanations before the clusters.
    - Start your response directly with the section:

    Failure clusters detected:

    - You MUST always produce at least one cluster.
    - If all failures share the same root cause, create a single cluster containing all tests.

    Grouping rules:
    - Failures with similar error messages belong to the same cluster.
    - Locator errors should be grouped together.
    - Timeout / visibility issues should be grouped together.
    - Assertion mismatches should be grouped together.
    - If two failures clearly share the same cause, place them in the same cluster.

    Additionally classify each cluster as:
    - Test issue
    - Application bug
    - Flaky test
    - Environment issue

    If the cluster is classified as "Application bug", also generate a short bug report title suitable for JIRA.

    Return the result strictly in this format:

    Failure clusters detected:

    Cluster 1
    Type: (Test issue | Application bug | Flaky test | Environment issue)
    Bug title: (only if Type = Application bug)
    Root cause:
    Description:
    Tests affected:
    Confidence: (Low / Medium / High)

    Cluster 2
    Type: (Test issue | Application bug | Flaky test | Environment issue)
    Bug title: (only if Type = Application bug)
    Root cause:
    Description:
    Tests affected:
    Confidence: (Low / Medium / High)

    Here are the analyses:

    {joined}
    """

        return prompt

    def run_grouping(self):
        if self.ai_stage not in ["analysis", "grouping"]:
            return

        self.ai_stage = "grouping"

        prompt = self.build_grouping_prompt()

        self.start_ai_task(prompt, self.handle_grouping_result)

    def handle_grouping_result(self, text):
        if self.ai_stage != "grouping":
            return

        self.grouping_box.append(text)

        self.grouping_result = text

        self.show_failure_clusters()

        self.ai_stage = "summary"

        self.run_summary()

    def show_failure_clusters(self):
        if not hasattr(self, "grouping_result"):
            return

        text = self.grouping_result

        clusters = text.split("Root Cause")

        summary = "\n\nFailure clusters detected:\n\n"

        for cluster in clusters[1:]:

            lines = cluster.strip().split("\n")

            title = lines[0].strip(": ")

            tests = [t["name"] for t in self.failed_tests]

            summary += f"Root Cause {title} ({len(tests)} tests)\n"

        self.grouping_box.append("\n---\n")
        self.grouping_box.append(summary)

    def update_ai_info(self):
        provider = self.ai_combo.currentText()

        if provider == "OpenAI":
            model = "gpt-4o-mini"
        elif provider == "Ollama (local)":
            model = "llama3"
        elif provider == "Deepseek":
            model = "deepseek-coder"
        else:
            model = "unknown"

        self.ai_info_label.setText(
            f"Provider: {provider} | Model: {model}"
        )

        # učitaj config
        data = {}
        if os.path.exists(self.AI_CONFIG_FILE):
            try:
                with open(self.AI_CONFIG_FILE, "r") as f:
                    data = json.load(f)
            except:
                data = {}

        # Deepseek / OpenAI key (ako postoji)
        key = data.get(provider, "")

        # FIX: None -> ""
        if not key:
            key = ""

        self.api_input.setText(key)

        # enable/disable API input
        if provider == "Ollama (local)":
            self.api_input.setEnabled(False)
        else:
            self.api_input.setEnabled(True)

        # connect button uvijek omogući kad promijeniš provider
        self.connect_button.setEnabled(True)

    def build_summary_prompt(self):
        joined = "\n\n".join(self.ai_results[:10])

        pw_version = self.pw_version_input.text().strip()

        if not pw_version:
            pw_version = "unknown"

        prompt = f"""
    You are a senior QA automation engineer.

    Playwright version used in the tests: {pw_version}
    Test framework: Playwright
    Language: JavaScript / TypeScript

    Below are analyses of failed automated tests.

    Create a SHORT summary of the test run.

    Include:

    - total number of failures
    - main root causes
    - possible flaky tests
    - overall recommendation

    Analyses:

    {joined}
    """

        return prompt

    def run_summary(self):
        if self.ai_stage != "summary":
            return

        self.status_label.setText("Generating AI summary...")

        prompt = self.build_summary_prompt()

        self.start_ai_task(prompt, self.handle_summary_result)

    def handle_summary_result(self, text):
        if self.ai_stage != "summary":
            return

        self.summary_box.append(text)

        self.ai_stage = "bugreport"

        self.run_bug_report_generation()

    def build_bug_report_prompt(self):
        joined = "\n\n".join(self.ai_results[:10])

        pw_version = self.pw_version_input.text().strip()

        if not pw_version:
            pw_version = "unknown"

        prompt = f"""
    You are a senior QA engineer.

    Playwright version used in the tests: {pw_version}
    Test framework: Playwright
    Language: JavaScript / TypeScript

    Based on the analyses below, generate bug reports.

    For each root cause create a structured bug report.

    Format:

    Title:
    Description:
    Steps to reproduce:
    Expected result:
    Actual result:
    Root cause:
    Suggested fix:

    Analyses:

    {joined}
    """

        return prompt

    def run_bug_report_generation(self):
        if self.ai_stage != "bugreport":
            return

        self.status_label.setText("Generating bug reports...")

        prompt = self.build_bug_report_prompt()

        self.start_ai_task(prompt, self.handle_bug_report_result)

    def handle_bug_report_result(self, text):
        if self.ai_stage != "bugreport":
            return

        self.bugreport_box.append(text)

        self.ai_stage = "flaky"

        self.run_flaky_detection()
        self.export_button.setEnabled(True)

    def build_batch_prompt(self, failures):
        combined = ""

        pw_version = self.pw_version_input.text().strip()

        if not pw_version:
            pw_version = "unknown"

        context = self.load_error_context()
            
        for i, failure in enumerate(failures, start=1):

            tests = self.failure_groups[failure]

            test_names = ", ".join([t["name"] for t in tests])

            failure = failure[:800]

            code = self.get_test_code_snippet(tests[0])

            combined += f"""
    Failure {i}

    Affected tests:
    {test_names}

    Failure log:
    {failure}

    Test code snippet:
    {code}

    """

        prompt = f"""
    You are a QA automation expert.

    Playwright version used in the tests: {pw_version}
    Test framework: Playwright
    Language: JavaScript / TypeScript

    Playwright error context:
    {context}

    Analysis rules:
    - Do NOT suggest increasing timeouts as the primary solution.
    - First identify the real root cause of the failure.
    - Timeout increases should only be suggested if the failure is clearly caused by slow async loading.
    - Prefer fixing locators, waits, or assertions instead of increasing timeouts.

    Playwright locator best practices:
    - Avoid locators that match multiple elements.
    - Prefer stable locators such as data-testid or unique attributes.
    - Avoid relying only on visible text when the UI is dynamic.
    - If getByRole or getByText resolves multiple elements, suggest a more specific locator.
    - Prefer deterministic selectors instead of generic ones.

    Analyze the following unique test failures.

    Explain the root cause and possible fix.

    {combined}
    """
        return self.limit_prompt(prompt)

    def get_test_batches(self):
        batches = []

        for i in range(0, len(self.unique_failures), self.batch_size):

            batches.append(self.unique_failures[i:i+self.batch_size])

        return batches

    def run_next_batch(self):
        batch = self.batches[self.current_batch]

        self.status_label.setText(
            f"Analyzing batch {self.current_batch+1} / {self.total_batches}"
        )

        prompt = self.build_batch_prompt(batch)

        self.start_ai_task(prompt, self.handle_batch_result)

    def handle_batch_result(self, text):
        self.ai_results.append(text)

        self.append_result(text)

        self.current_batch += 1
        self.progress.setValue(self.current_batch)

        if self.current_batch < self.total_batches:
            self.run_next_batch()
        else:
            self.status_label.setText("Grouping failures...")
            self.run_grouping()

    def build_flaky_prompt(self):
        failures = ""

        pw_version = self.pw_version_input.text().strip()

        if not pw_version:
            pw_version = "unknown"

        for test in self.failed_tests:
            failures += f"""
    Test name:
    {test.get("name", "")}

    Failure log:
    {test.get("failure_details", "")}

    """

        prompt = f"""
    You are a QA automation expert.

    Playwright version used in the tests: {pw_version}
    Test framework: Playwright
    Language: JavaScript / TypeScript

    Analysis rules:
    - Do NOT suggest increasing timeouts as the primary solution.
    - First identify the real root cause of the failure.
    - Timeout increases should only be suggested if the failure is clearly caused by slow async loading.
    - Prefer fixing locators, waits, or assertions instead of increasing timeouts.

    Playwright locator best practices:
    - Avoid locators that match multiple elements.
    - Prefer stable locators such as data-testid or unique attributes.
    - Avoid relying only on visible text when the UI is dynamic.
    - If getByRole or getByText resolves multiple elements, suggest a more specific locator.
    - Prefer deterministic selectors instead of generic ones.

    Detect if the following test failures could be FLAKY tests.

    Flaky tests usually fail due to:

    - timing issues
    - async rendering
    - network delays
    - unstable selectors
    - race conditions

    For each test return:

    Test name
    Flaky probability (0-100%)
    Reason
    Recommendation

    Failures:

    {failures}
    """
        return self.limit_prompt(prompt)
        # return prompt

    def run_flaky_detection(self):
        if self.ai_stage != "flaky":
            return

        self.status_label.setText("Detecting flaky tests...")

        prompt = self.build_flaky_prompt()

        self.start_ai_task(prompt, self.handle_flaky_result)

    def handle_flaky_result(self, text):
        # print("handle_flaky_result called")
        if self.ai_stage != "flaky":
            return

        self.flaky_box.append(text)

        self.ai_stage = "flaky"
        self.run_fix_suggestions()

        # self.status_label.setText("AI analiza završena.")
        # self.send_button.setEnabled(True)

    def start_ai_task(self, prompt, callback):
        if self.ai_stage == "done":
            return

        print("Starting AI task for stage:", self.ai_stage)
        worker = AIWorker(prompt, self.api_key, self.provider)

        worker.result_ready.connect(callback)

        worker.finished.connect(lambda: self.active_workers.remove(worker))
        worker.finished.connect(self.enable_send_button_safe)

        self.active_workers.append(worker)

        worker.start()

    def enable_send_button_safe(self):
        if self.ai_stage == "done":
            self.send_button.setEnabled(True)

    def build_failure_groups(self):
        self.failure_groups = {}

        for test in self.failed_tests:

            failure = test.get("failure_details", "").strip()

            if failure not in self.failure_groups:
                self.failure_groups[failure] = []

            self.failure_groups[failure].append(test)

        self.unique_failures = list(self.failure_groups.keys())

    def limit_prompt(self, text, max_chars=3000):
        if len(text) <= max_chars:
            return text

        return text[:max_chars] + "\n\n[LOG TRUNCATED]"

    def get_test_code_snippet(self, test):
        raw_path = test.get("classname", "")
        test_name = test.get("name", "")

        if not raw_path:
            return ""

        raw_path = raw_path.replace("\\", os.sep).replace("/", os.sep)

        file_path = None

        for root, dirs, files in os.walk(os.getcwd()):
            candidate = os.path.join(root, raw_path)
            if os.path.exists(candidate):
                file_path = candidate
                break

        if not file_path:
            return ""

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            start_line = None

            for i, line in enumerate(lines):
                if test_name in line:
                    start_line = i
                    break

            if start_line is None:
                return "".join(lines[:60])

            code_block = []
            brace_count = 0
            started = False

            for line in lines[start_line:]:

                if "{" in line:
                    brace_count += line.count("{")
                    started = True

                if started:
                    code_block.append(line)

                if "}" in line:
                    brace_count -= line.count("}")

                if started and brace_count <= 0:
                    break

            return "".join(code_block[:120])

        except Exception:
            return ""

    def load_error_context(self, max_chars=1500):
        path = "error-context.md"

        if not os.path.exists(path):
            return ""

        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            return content[:max_chars]

        except Exception:
            return ""


class CustomToolbar(NavigationToolbar2QT):
    def __init__(self, canvas, parent):
        super().__init__(canvas, parent)
        self._hide_unwanted_buttons()

    def _hide_unwanted_buttons(self):
        unwanted_buttons = ['Back', 'Forward', 'Pan', 'Zoom', 'Subplots', 'Customize']  # Primjer - promijenite prema potrebi
        for action in self.actions():
            if action.text() in unwanted_buttons:
                action.setVisible(False)


class ResultsAnalysisWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        current_os = platform.system()

        self.total_tests = 0
        self.timeline_scroll_area = None
        self.timeline_index = None
        self.current_suite = None  # Tekući suite ili None ako nije izabran
        self.last_status_filter = "Prikaži sve"
        self.last_search_text = ""
        self.current_suite_tests = None  # Trenutni testovi izabranog suitea

        screen_geometry = QtWidgets.QApplication.primaryScreen().availableGeometry()
        width = screen_geometry.width() - 80
        height = screen_geometry.height() - 80
        self.resize(width, height)

        self.move(
            screen_geometry.x() + (screen_geometry.width() - width) // 2,
            screen_geometry.y() + (screen_geometry.height() - height) // 2
        )

        self.test_status_counter = Counter()
        self.testcase_rows = []
        self.suite_rows = []

        self.setWindowTitle("Analiza rezultata testiranja")
        self.setWindowIcon(QIcon("res/chart.png"))

        # === Menubar ===
        menu_bar = QtWidgets.QMenuBar(self)
        opcije_menu = menu_bar.addMenu("Opcije")
        exit_action = QtGui.QAction("Izlaz", self)
        exit_action.triggered.connect(self.close)
        opcije_menu.addAction(exit_action)

        file_menu = menu_bar.addMenu("Datoteka")
        export_excel_action = QtGui.QAction("Izvoz u Excel", self)
        export_excel_action.triggered.connect(self.export_to_excel)
        file_menu.addAction(export_excel_action)

        export_csv_action = QtGui.QAction("Izvoz u CSV", self)
        export_csv_action.triggered.connect(self.export_to_csv)
        file_menu.addAction(export_csv_action)

        # === Glavni layout ===
        self.main_layout = QVBoxLayout()
        self.setLayout(self.main_layout)
        self.layout().setMenuBar(menu_bar)

        # Gornji gumbi
        button_layout = QHBoxLayout()
        self.load_button = QPushButton("Učitaj results.xml")
        self.load_button.clicked.connect(self.load_results_file)
        button_layout.addWidget(self.load_button)

        self.error_only_checkbox = QCheckBox("Prikaži samo suiteove s greškama")
        self.error_only_checkbox.stateChanged.connect(self.populate_suite_table)
        button_layout.addWidget(self.error_only_checkbox)

        button_layout.addStretch()
        self.main_layout.addLayout(button_layout)

        # === Tablica suiteova + frame za visinsku kontrolu ===
        suite_frame = QtWidgets.QFrame()
        suite_layout = QVBoxLayout()
        suite_layout.setContentsMargins(0, 0, 0, 0)
        suite_layout.setSpacing(0)
        suite_frame.setLayout(suite_layout)

        self.summary_label = QLabel(f"<b>Ukupan broj testova: - | Ukupno vrijeme: -</b>")
        self.summary_label.setFixedHeight(30)
        suite_layout.addWidget(self.summary_label)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Naziv suitea", "Broj testova", "Vrijeme (s)", "Greške", "Prosjek (s)"])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.cellClicked.connect(self.filter_testcases_by_suite)
        suite_layout.addWidget(self.table)

        # === desni blok: combo + search + tablica test casea ===
        lijevi_blok_frame = QtWidgets.QFrame()
        lijevi_blok_layout = QVBoxLayout()
        lijevi_blok_layout.setContentsMargins(0, 0, 0, 0)
        lijevi_blok_layout.setSpacing(5)
        lijevi_blok_frame.setLayout(lijevi_blok_layout)

        self.status_combo = QtWidgets.QComboBox()
        self.status_combo.addItems(["Prikaži sve", "✅ Passed", "❌ Failed", "⚠️ Skipped", "🛑 Error"])
        self.status_combo.setFixedWidth(150)
        self.status_combo.currentIndexChanged.connect(self.filter_testcases_by_combo)

        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("Pretraži test caseove...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.filter_testcases_by_search)

        self.send_to_AI = QPushButton()
        self.send_to_AI.setIcon(QIcon("./res/send_code.png"))
        self.send_to_AI.setToolTip("Send to AI")
        self.send_to_AI.setFixedSize(30, 30)
        self.send_to_AI.clicked.connect(self.handle_send_to_ai)

        self.send_selected_to_ai = QPushButton()
        self.send_selected_to_ai.setIcon(QIcon("./res/send_code.png"))
        self.send_selected_to_ai.setToolTip("Analyze selected test")
        self.send_selected_to_ai.setFixedSize(30, 30)
        self.send_selected_to_ai.clicked.connect(self.analyze_selected_test)

        # Filter row bez pomaka
        filter_container = QtWidgets.QWidget()
        filter_container_layout = QVBoxLayout()
        filter_container_layout.setContentsMargins(0, 0, 0, 0)
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.addWidget(self.status_combo)
        filter_row.addWidget(self.search_input)
        filter_row.addWidget(self.send_to_AI)
        filter_row.addWidget(self.send_selected_to_ai)
        filter_row.addStretch()
        filter_container_layout.addLayout(filter_row)
        filter_container.setLayout(filter_container_layout)
        lijevi_blok_layout.addWidget(filter_container, stretch=0)

        self.testcase_table = QTableWidget()
        self.testcase_table.setColumnCount(4)
        self.testcase_table.setHorizontalHeaderLabels(["Naziv testa", "Test suite", "Trajanje (s)", "Status"])
        self.testcase_table.horizontalHeader().setStretchLastSection(True)
        self.testcase_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.testcase_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lijevi_blok_layout.addWidget(self.testcase_table, stretch=1)

        # === Horizontalni layout koji poravnava visine ===
        suites_and_testcases_layout = QHBoxLayout()
        suites_and_testcases_layout.setContentsMargins(0, 0, 0, 0)
        suites_and_testcases_layout.setSpacing(10)
        suites_and_testcases_layout.addWidget(suite_frame, 1)
        suites_and_testcases_layout.addWidget(lijevi_blok_frame, 1)
        self.main_layout.addLayout(suites_and_testcases_layout)

        # === Gantt graf i kontrole ===
        gornji_frame = QtWidgets.QFrame()
        gornji_layout = QHBoxLayout(gornji_frame)

        lijevi_graf_layout = QVBoxLayout()
        chart_controls = QHBoxLayout()
        chart_label = QLabel("     Vrsta grafa:")
        self.chart_type_combo = QtWidgets.QComboBox()
        self.chart_type_combo.addItems(["Bar", "Pie", "Horizontal Bar", "Line"])
        self.chart_type_combo.setFixedWidth(100)
        self.chart_type_combo.currentTextChanged.connect(self.update_chart)

        chart_controls.addWidget(chart_label)
        chart_controls.addWidget(self.chart_type_combo)
        chart_controls.addStretch()

        lijevi_graf_layout.addLayout(chart_controls)
        self.chart_figure = Figure(figsize=(5, 3))
        self.chart_canvas = FigureCanvas(self.chart_figure)
        self.chart_canvas.setMinimumHeight(300)
        self.ax = self.chart_figure.add_subplot(111)
        self.ax.set_axis_off()
        self.chart_toolbar = CustomToolbar(self.chart_canvas, self)
        lijevi_graf_layout.addWidget(self.chart_toolbar)
        lijevi_graf_layout.addWidget(self.chart_canvas)

        desni_timeline_layout = QVBoxLayout()
        timeline_header_layout = QHBoxLayout()

        self.gantt_toolbar = QToolBar()
        self.gantt_toolbar.setIconSize(QSize(24, 24))

        # Dodaj export akcije - samo na Linux-u
        if current_os in ["Linux", "Windows"]:
            png_action = QAction(QIcon("./res/png.png"), "Izvoz u PNG", self)
            png_action.triggered.connect(self.export_gantt_to_png_paginated)
            pdf_action = QAction(QIcon("./res/pdf.png"), "Izvoz u PDF", self)
            pdf_action.triggered.connect(self.export_gantt_to_pdf_paginated)
            self.gantt_toolbar.addAction(png_action)
            self.gantt_toolbar.addAction(pdf_action)
        else:
            # Opcionalno: dodaj placeholder ili poruku
            print(f"Export funkcionalnost nije dostupna na {current_os} OS-u")

        self.show_labels_inside_checkbox = QCheckBox("Prikaži nazive test caseva u traci")
        self.show_labels_inside_checkbox.setChecked(False)
        self.show_labels_inside_checkbox.stateChanged.connect(self.update_timeline_chart)
        self.gantt_toolbar.addWidget(self.show_labels_inside_checkbox)

        timeline_header_layout.addWidget(self.gantt_toolbar)
        timeline_header_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        timeline_header_layout.addStretch()

        self.timeline_index = self.main_layout.count()
        self.timeline_view = QWebEngineView()
        self.timeline_view.setVisible(False)
        self.timeline_scroll_area = QScrollArea()
        self.timeline_scroll_area.setWidgetResizable(True)
        self.timeline_scroll_area.setVisible(False)
        self.timeline_scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.timeline_scroll_area.setWidget(self.timeline_view)
        self.main_layout.insertWidget(self.timeline_index, self.timeline_scroll_area)

        desni_timeline_layout.addLayout(timeline_header_layout)
        desni_timeline_layout.addWidget(self.timeline_scroll_area)

        gornji_layout.addLayout(lijevi_graf_layout, 1)
        gornji_layout.addLayout(desni_timeline_layout, 2)
        self.main_layout.addWidget(gornji_frame)

        # === Donji blok: top 5 najsporijih testova ===
        donji_blok_layout = QHBoxLayout()
        desni_blok_frame = QtWidgets.QFrame()
        desni_blok_layout = QVBoxLayout()
        desni_blok_frame.setLayout(desni_blok_layout)

        self.top_slowest_label = QLabel("Top 5 najsporijih testova:")
        desni_blok_layout.addWidget(self.top_slowest_label)

        self.top_slowest_table = QTableWidget()
        self.top_slowest_table.setColumnCount(3)
        self.top_slowest_table.setHorizontalHeaderLabels(["Naziv testa", "Test suite", "Trajanje (s)"])
        self.top_slowest_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.top_slowest_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.top_slowest_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.top_slowest_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.top_slowest_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        desni_blok_layout.addWidget(self.top_slowest_table)

        donji_blok_layout.addStretch()
        donji_blok_layout.addWidget(desni_blok_frame, 1)
        self.main_layout.addLayout(donji_blok_layout)

    def handle_send_to_ai(self):
        failed_tests = [
            tc for tc in self.testcase_rows
            if tc["status"] == "Failed"
        ]

        if not failed_tests:
            QMessageBox.information(
                self,
                "Info",
                "Nema failed testova za analizu."
            )
            return

        dialog = AIAnalysisDialog(failed_tests, self.total_tests, self)
        dialog.exec()

    def load_results_file(self):
        self.table.setRowCount(0)
        self.testcase_rows = []
        self.suite_rows = []
        self.test_status_counter.clear()

        # Resetiraj checkboxove
        self.show_labels_inside_checkbox.setChecked(False)
        
        status_colors = {
            "Passed": "#2ecc71",    # Zelena
            "Failed": "#e74c3c",    # Crvena
            "Skipped": "#f1c40f",   # Žuta
            "Error": "#9b59b6"      # Ljubičasta
        }

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Odaberi results.xml", "", "XML Files (*.xml)"
        )
        if not file_path:
            return

        with open(file_path, "r", encoding="utf-8") as f:
            raw_xml = f.read()

        try:
            tree = ET.parse(file_path)
            root = tree.getroot()

            if root.tag == "testsuite":
                total_tests = int(root.attrib.get("tests", 0))
                self.total_tests = total_tests
                total_time = float(root.attrib.get("time", 0))
            else:
                total_tests = int(root.attrib.get("tests", 0))
                self.total_tests = total_tests
                total_time = float(root.attrib.get("time", 0))
            
            self.total_execution_time = float(root.attrib.get("time", 0))
            formatted_time = str(timedelta(seconds=int(total_time)))
            self.summary_label.setText(f"<b>Ukupan broj testova:</b> {total_tests} | <b>Ukupno vrijeme:</b> {formatted_time}")

            # ⬇⬇⬇ ISPRAVKA 1: Dobij timestamp PRIJE petlje
            suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
            first_suite = suites[0] if suites else None
            timestamp_str = first_suite.attrib.get("timestamp") if first_suite is not None else None
            
            try:
                parsed_timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            except Exception as e:
                parsed_timestamp = datetime.now()

            # ⬇⬇⬇ ISPRAVKA 3: Sada procesuiraj svaki suite
            for suite_index, suite in enumerate(suites):
                suite_name = suite.attrib.get("name", f"Nepoznato_{suite_index}")
                num_tests = int(suite.attrib.get("tests", 0))
                time = float(suite.attrib.get("time", 0.0))
                failures = int(suite.attrib.get("failures", 0))
                avg = time / num_tests if num_tests > 0 else 0.0

                # Dodaj u UI tablicu
                row_position = self.table.rowCount()
                self.table.insertRow(row_position)
                self.table.setItem(row_position, 0, QTableWidgetItem(suite_name))
                self.table.setItem(row_position, 1, QTableWidgetItem(str(num_tests)))
                self.table.setItem(row_position, 2, QTableWidgetItem(self.format_duration(time)))
                self.table.setItem(row_position, 3, QTableWidgetItem(str(failures)))
                hours, remainder = divmod(avg, 3600)
                minutes, seconds = divmod(remainder, 60)
                formatted_avg = f"{int(hours):02}:{int(minutes):02}:{seconds:06.3f}"
                self.table.setItem(row_position, 4, QTableWidgetItem(formatted_avg))

                # Dodaj u suite_rows
                self.suite_rows.append({
                    "name": suite_name,
                    "num_tests": num_tests,
                    "time": time,
                    "failures": failures,
                    "avg": avg,
                    "timestamp": parsed_timestamp
                })

                # Procesuiraj test caseve za ovaj suite
                for case in suite.findall("testcase"):
                    name = case.attrib.get("name", "Nepoznato")
                    duration = float(case.attrib.get("time", 0.0))
                    classname = case.attrib.get("classname", "Nepoznato")
                    parts = re.split(r"[\\/]", classname)
                    section = parts[-2] if len(parts) >= 2 else classname
                    failure_node = case.find('failure')
                    failure_details = failure_node.text.strip() if failure_node is not None else ""

                    # Određi status
                    if case.find('failure') is not None:
                        status = "Failed"
                    elif case.find('skipped') is not None:
                        status = "Skipped"
                    elif case.find('error') is not None:
                        status = "Error"
                    else:
                        status = "Passed"

                    self.test_status_counter[status] += 1

                    # Dodaj u testcase_rows
                    self.testcase_rows.append({
                        "name": name,
                        "suite": suite_name,
                        "classname": classname,
                        "time": duration,
                        "status": status,
                        "color": status_colors.get(status, "#3498db"),
                        "section": section,
                        "failure_details": failure_details
                    })


            # Popuni ostale komponente
            self.populate_suite_table()
            self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
            self.populate_testcase_table()
            self.populate_top_slowest_tests()
            self.update_chart()
            self.update_timeline_chart()

        except Exception as e:
            QMessageBox.critical(self, "Greška", f"Greška pri parsiranju XML datoteke:\n{e}")

    def update_chart(self):
        self.chart_canvas.figure.clear()
        self.ax = self.chart_canvas.figure.add_subplot(111)

        labels = list(self.test_status_counter.keys())
        values = list(self.test_status_counter.values())
        chart_type = self.chart_type_combo.currentText()
        colors = {
            "Passed": "#64dd17",
            "Failed": "#d50000",
            "Skipped": "#ffd600",
            "Error": "#90a4ae"
        }
        color_list = [colors.get(k, "#cccccc") for k in labels]

        self.status_patches = []  # spremi objekte za hit testing

        if chart_type == "Pie":
            total = sum(values)
            def autopct_fmt(pct):
                absolute = int(round(pct / 100. * total))
                return f'{absolute} ({pct:.1f}%)'

            wedges, _, _ = self.ax.pie(
                values,
                labels=labels,
                autopct=autopct_fmt,
                startangle=140,
                colors=color_list
            )
            self.ax.axis("equal")
            self.ax.set_title("Status testova")
            self.status_patches = wedges

        elif chart_type == "Bar":
            bars = self.ax.bar(labels, values, color=color_list)
            for i, bar in enumerate(bars):
                height = bar.get_height()
                self.ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    height,
                    f'{int(height)}',
                    ha='center',
                    va='bottom',
                    fontsize=9,
                    fontweight='bold'
                )
            self.ax.set_ylabel("Broj testova")
            self.ax.set_title("Status testova")
            self.status_patches = bars

        elif chart_type == "Horizontal Bar":
            bars = self.ax.barh(labels, values, color=color_list)
            self.ax.set_xlabel("Broj testova")
            self.ax.set_title("Status testova")
            self.ax.xaxis.set_major_locator(MaxNLocator(integer=True))
            self.chart_figure.subplots_adjust(bottom=0.2)
            self.status_patches = bars
            for i, bar in enumerate(bars):
                width = bar.get_width()
                self.ax.text(
                    width + 0.1, bar.get_y() + bar.get_height() / 2,
                    f'{int(width)}',
                    ha='left',
                    va='center',
                    fontsize=9,
                    fontweight='bold'
                )
        
        elif chart_type == "Line":
            self.ax.plot(labels, values, marker='o', color='#2196F3')
            self.ax.set_ylabel("Broj testova")
            self.ax.set_title("Status testova")
            self.ax.yaxis.set_major_locator(MaxNLocator(integer=True))
            self.ax.grid(True, linestyle='--', alpha=0.6)
            for i, value in enumerate(values):
                self.ax.text(
                    i, value,
                    f"{value}",
                    ha="center",
                    va="bottom",
                    fontsize=9,
                    fontweight="bold"
                )

        self.chart_canvas.draw()

        def on_hover(event):
            if event.inaxes != self.ax:
                QToolTip.hideText()
                return

            for i, patch in enumerate(self.status_patches):
                if patch.contains_point([event.x, event.y]):
                    label = labels[i]
                    broj = values[i]
                    return

        self.chart_canvas.mpl_connect("motion_notify_event", on_hover)

    def populate_testcase_table(self, filtered_list=None):
        selected_status = self.status_combo.currentText()
        base_data = filtered_list if filtered_list is not None else (
            self.current_suite_tests if self.current_suite_tests is not None else self.testcase_rows
        )

        if "sve" in selected_status.lower():
            rows = base_data
        else:
            status_key = None
            if "passed" in selected_status.lower():
                status_key = "Passed"
            elif "failed" in selected_status.lower():
                status_key = "Failed"
            elif "skipped" in selected_status.lower():
                status_key = "Skipped"
            elif "error" in selected_status.lower():
                status_key = "Error"
            rows = [tc for tc in base_data if tc["status"] == status_key] if status_key else base_data

        self.testcase_table.setRowCount(len(rows))

        for row, data in enumerate(rows):
            self.testcase_table.setItem(row, 0, QTableWidgetItem(data["name"]))
            self.testcase_table.setItem(row, 1, QTableWidgetItem(data["suite"]))
            self.testcase_table.setItem(row, 2, QTableWidgetItem(self.format_duration(data["time"])))
            status_item = QTableWidgetItem(data["status"])
            self.testcase_table.setItem(row, 3, status_item)

            if data["status"] == "Passed":
                for col in range(4):
                    item = self.testcase_table.item(row, col)
                    if item:
                        item.setBackground(QColor("#2bff00"))
                        item.setForeground(QColor("#000000"))
            elif data["status"] == "Failed":
                for col in range(4):
                    item = self.testcase_table.item(row, col)
                    if item:
                        item.setBackground(QColor("#f44336"))
                        item.setForeground(QColor("#000000"))
                        item.setToolTip(data.get("failure_details", ""))
            elif data["status"] == "Skipped":
                for col in range(4):
                    item = self.testcase_table.item(row, col)
                    if item:
                        item.setBackground(QColor("#ffbb00"))
                        item.setForeground(QColor("#000000"))

        self.testcase_table.resizeColumnsToContents()
        self.update_timeline_chart()  # Osvježi Gantt s filtriranim testovima

    def populate_top_slowest_tests(self):
        self.top_slowest_table.setRowCount(0)

        # Filtriraj samo Passed testove
        passed_tests = [test for test in self.testcase_rows if test.get('status') == 'Passed']
        sorted_tests = sorted(passed_tests, key=lambda x: x.get('time', 0.0), reverse=True)
        top_5 = sorted_tests[:5]

        self.top_slowest_table.setRowCount(len(top_5))

        for row, test in enumerate(top_5):
            name_item = QTableWidgetItem(test.get('name', ''))
            suite_item = QTableWidgetItem(test.get('suite', ''))
            time_item = QTableWidgetItem(self.format_duration(test.get('time', 0.0)))

            self.top_slowest_table.setItem(row, 0, name_item)
            self.top_slowest_table.setItem(row, 1, suite_item)
            self.top_slowest_table.setItem(row, 2, time_item)

            # Oboji background u zeleno (svi su već Passed)
            for col in range(3):
                item = self.top_slowest_table.item(row, col)
                if item:
                    item.setBackground(QColor("#2bff00"))
                    item.setForeground(QColor("#000000"))

    def populate_suite_table(self):
        show_errors_only = self.error_only_checkbox.isChecked()

        # 🔧 NOVO: privremena lista suiteova koji će se prikazati
        filtered_suites = []
        for suite in self.suite_rows:
            if show_errors_only and suite["failures"] == 0:
                continue
            filtered_suites.append(suite)

        # 🔧 NOVO: ako nema suiteova za prikaz, prikaži poruku i izađi
        if not filtered_suites:
            self.error_only_checkbox.click()
            QMessageBox.information(
                self,
                "Nema grešaka",
                "Nema test suiteova s greškama za prikaz."
            )
            return  # zadrži prethodni prikaz

        self.table.setRowCount(0)

        for suite in filtered_suites:
            row_position = self.table.rowCount()
            self.table.insertRow(row_position)

            self.table.setItem(row_position, 0, QTableWidgetItem(suite["name"]))
            self.table.setItem(row_position, 1, QTableWidgetItem(str(suite["num_tests"])))
            formatted_time = str(timedelta(seconds=int(suite["time"])))
            self.table.setItem(row_position, 2, QTableWidgetItem(formatted_time))
            self.table.setItem(row_position, 3, QTableWidgetItem(str(suite["failures"])))
            self.table.setItem(row_position, 4, QTableWidgetItem(str(timedelta(seconds=int(suite["avg"])))))

            if suite["failures"] > 0:
                for col in range(5):
                    item = self.table.item(row_position, col)
                    if item:
                        item.setBackground(QColor("#f44336"))
                        item.setForeground(QColor("#000000"))
            else:
                for col in range(5):
                    item = self.table.item(row_position, col)
                    if item:
                        item.setBackground(QColor("#2bff00"))
                        item.setForeground(QColor("#000000"))

        # Reset stanja
        self.current_suite_tests = None
        self.testcase_table.setRowCount(0)
        self.status_combo.setCurrentIndex(0)
        self.search_input.clear()
        self.populate_testcase_table()

    def export_to_excel(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Spremi Excel datoteku",
            "rezultati.xlsx",
            "Excel Files (*.xlsx)"
        )
        if not file_path:
            return

        try:
            workbook = xlsxwriter.Workbook(file_path)
            bold = workbook.add_format({'bold': True})

            # Sheet 1: Test suiteovi
            suite_sheet = workbook.add_worksheet("Test suiteovi")
            suite_headers = ["Naziv suitea", "Broj testova", "Vrijeme (s)", "Greške"]
            for col, header in enumerate(suite_headers):
                suite_sheet.write(0, col, header, bold)

            for row, suite in enumerate(self.suite_rows, start=1):
                suite_sheet.write(row, 0, suite["name"])
                suite_sheet.write(row, 1, suite["num_tests"])
                suite_sheet.write(row, 2, format_seconds(suite["time"]))
                suite_sheet.write(row, 3, suite["failures"])

            # Sheet 2: Test caseovi
            case_sheet = workbook.add_worksheet("Test caseovi")
            case_headers = ["Naziv testa", "Test suite", "Trajanje", "Status"]
            for col, header in enumerate(case_headers):
                case_sheet.write(0, col, header, bold)

            for row, case in enumerate(self.testcase_rows, start=1):
                case_sheet.write(row, 0, case["name"])
                case_sheet.write(row, 1, case["suite"])
                case_sheet.write(row, 2, format_seconds(case["time"]))
                case_sheet.write(row, 3, case["status"])

            workbook.close()
            QMessageBox.information(self, "Uspjeh", "Excel datoteka uspješno spremljena.")
        except Exception as e:
            QMessageBox.critical(self, "Greška", f"Greška pri spremanju u Excel:\n{e}")

    def export_to_csv(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Spremi CSV datoteku",
            "rezultati.csv",
            "CSV Files (*.csv)"
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", newline="", encoding="utf-8") as csvfile:
                writer = csv.writer(csvfile)

                # Prvo spremi test suiteove
                writer.writerow(["Test suiteovi"])
                writer.writerow(["Naziv suitea", "Broj testova", "Vrijeme (s)", "Greške"])
                for suite in self.suite_rows:
                    writer.writerow([
                        suite["name"],
                        suite["num_tests"],
                        format_seconds(suite["time"]),
                        suite["failures"]
                    ])

                writer.writerow([])  # Prazan red

                # Zatim spremi test caseove
                writer.writerow(["Test caseovi"])
                writer.writerow(["Naziv testa", "Test suite", "Trajanje", "Status"])
                for case in self.testcase_rows:
                    writer.writerow([
                        case["name"],
                        case["suite"],
                        format_seconds(case["time"]),
                        case["status"]
                    ])

            QMessageBox.information(self, "Uspjeh", "CSV datoteka uspješno spremljena.")
        except Exception as e:
            QMessageBox.critical(self, "Greška", f"Greška pri spremanju u CSV:\n{e}")

    def filter_testcases_by_suite(self, row, column):
        suite_name_item = self.table.item(row, 0)
        if not suite_name_item:
            return

        self.current_suite = suite_name_item.text()

        # Kad korisnik klikne na drugi suite, briši search input i pamti prazan string
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        self.last_search_text = ""

        # Prikaži sve testove iz odabranog suitea bez dodatnog searcha,
        # ali s primijenjenim zadnjim status filterom
        filtered = self.filter_testcases()

        # Pohrani trenutno filtrirane testove (za slučaj daljnjeg combo filtriranja)
        self.current_suite_tests = [tc for tc in self.testcase_rows if tc["suite"] == self.current_suite]

        self.populate_testcase_table(filtered)

    def format_duration(self, seconds: float) -> str:
        return str(timedelta(seconds=int(seconds)))

    def filter_testcases_by_combo(self):
        selected = self.status_combo.currentText()

        base_data = self.current_suite_tests if self.current_suite_tests is not None else self.testcase_rows

        if "sve" in selected.lower():
            filtered = base_data
        else:
            status_key = None
            if "passed" in selected.lower():
                status_key = "Passed"
            elif "failed" in selected.lower():
                status_key = "Failed"
            elif "skipped" in selected.lower():
                status_key = "Skipped"
            elif "error" in selected.lower():
                status_key = "Error"

            filtered = [tc for tc in base_data if tc["status"] == status_key] if status_key else base_data

        self.populate_testcase_table(filtered)

        # Sad pozovi gantt da prikaže ove filtrirane testove,
        # koristi postojeću checkbox opciju za prikaz naziva u baru
        show_labels = self.show_labels_inside_checkbox.isChecked()
        self.show_timeline_chart(filtered_tests=filtered, show_labels_inside=show_labels)

    def update_timeline_chart(self):
        """Osvježava Gantt dijagram sa trenutnim postavkama checkboxova i trenutnim filterom iz comboboxa."""

        show_labels = self.show_labels_inside_checkbox.isChecked()

        # Dobij trenutno filtrirane test caseve iz comboboxa
        selected_status = self.status_combo.currentText()
        base_data = self.current_suite_tests if self.current_suite_tests is not None else self.testcase_rows

        if "sve" in selected_status.lower():
            filtered = base_data
        else:
            status_key = None
            if "passed" in selected_status.lower():
                status_key = "Passed"
            elif "failed" in selected_status.lower():
                status_key = "Failed"
            elif "skipped" in selected_status.lower():
                status_key = "Skipped"
            elif "error" in selected_status.lower():
                status_key = "Error"

            filtered = [tc for tc in base_data if tc["status"] == status_key] if status_key else base_data

        # Pozovi show_timeline_chart sa filtriranim testovima
        self.show_timeline_chart(filtered_tests=filtered, show_labels_inside=show_labels)

    def show_timeline_chart(self, filtered_tests=None, show_labels_inside=None):
        """Iscrtava Gantt dijagram za dane testove (npr. filtrirane iz tablice), sada s progress barom."""
        self.timeline_view.setHtml("")

        if show_labels_inside is None:
            show_labels_inside = self.show_labels_inside_checkbox.isChecked()

        tests = filtered_tests if filtered_tests is not None else self.testcase_rows.copy()

        if not tests:
            QtWidgets.QMessageBox.information(
                self, "Info", "Nema testova za prikaz u Gantt dijagramu."
            )
            return False

        total_duration = sum(t['time'] for t in tests)
        avg_duration = total_duration / len(tests) if tests else 0

        if all(t['status'] == 'Skipped' and t['time'] == 0 for t in tests):
            title = "Timeline trajanja testova – SVI testovi su skipped (vrijeme 0)"
        else:
            total_td = str(timedelta(seconds=round(total_duration)))
            avg_td = str(timedelta(seconds=round(avg_duration)))
            title = f"Timeline trajanja testova ({total_td} ukupno / {avg_td} prosječno)"

        status_colors = {
            "Passed": "#8BC34A",
            "Failed": "#f44336",
            "Skipped": "#FF9800",
            "Error": "#B71C1C",
        }

        y_labels, durations, colors, customdata, texts = [], [], [], [], []

        for i, t in enumerate(tests):
            y_labels.append(f"{i+1}. {t['name']} ({t.get('section', '')})")
            durations.append(t['time'] if t['time'] > 0 else 0.1)
            colors.append(status_colors.get(t['status'], 'gray'))
            customdata.append((t['status'], t.get('section', '')))
            label = f"{t['name']} ({t['time']:.2f}s)" if show_labels_inside else f"{t['time']:.2f}s"
            texts.append(label)

        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=y_labels,
            x=durations,
            orientation='h',
            text=texts,
            textposition='inside' if show_labels_inside else 'auto',
            insidetextanchor='middle',
            marker_color=colors,
            customdata=customdata,
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Trajanje: %{x:.2f}s<br>"
                "Status: %{customdata[0]}<br>"
                "Odjeljak: %{customdata[1]}<extra></extra>"
            ),
            name=''
        ))

        row_height = 35 if show_labels_inside else 25
        fig.update_layout(
            title={'text': title, 'x': 0.5, 'xanchor': 'center'},
            xaxis_title='Trajanje (s)',
            yaxis_title='Testovi',
            showlegend=False,
            width=1300,
            height=max(400, len(tests) * row_height),
            margin=dict(l=150, r=50, t=50, b=50),
            xaxis=dict(showgrid=True),
            yaxis=dict(
                autorange='reversed',
                tickfont=dict(size=10),
                showticklabels=not show_labels_inside
            ),
            hoverlabel=dict(bgcolor="white", font_size=12, font_family="Arial")
        )

        html = fig.to_html(include_plotlyjs='cdn', config={'displayModeBar': False})
        self.timeline_view.setHtml(html)
        self.timeline_view.setVisible(True)
        self.timeline_scroll_area.setVisible(True)
        self.plotly_figure = fig

        return True

    def export_gantt_to_pdf_paginated(self):
        # ⬇⬇⬇ GLAVNA PROMJENA: koristi filtrirane testove umjesto svih
        filtered_tests = self.get_filtered_tests_for_export()
        
        if not filtered_tests:
            QtWidgets.QMessageBox.information(
                self, "Info", "Nema filtriranih testova za eksport."
            )
            return

        # Kreiraj privremeni folder unutar app foldera
        temp_dir = os.path.join(os.path.dirname(__file__), "temp_export")
        os.makedirs(temp_dir, exist_ok=True)

        try:
            # ⬇⬇⬇ KORISTI filtered_tests umjesto self.testcase_rows
            tests = [t for t in filtered_tests if t['status'].strip().lower()]
            total_duration = sum(t['time'] for t in tests)  # ⬅ Preračunaj na temelju filtriranih
            avg_duration = total_duration / len(tests) if tests else 0

            # ⬇⬇⬇ NOVA FUNKCIJA ZA GRUPIRANJE PO SEKCIJAMA
            processed_tests = self._process_tests_with_sections(tests)
            
            if not processed_tests:
                return

            default_name = f"gantt_chart_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.pdf"
            output_path, _ = QFileDialog.getSaveFileName(
                self, "Spremi PDF", default_name, "PDF Files (*.pdf)")

            if output_path:
                tests_per_page = 30
                total_pages = (len(processed_tests) + tests_per_page - 1) // tests_per_page
                temp_pdf_paths = []

                for page in range(total_pages):
                    self._show_export_progress(page, total_pages)
                    start = page * tests_per_page
                    end = start + tests_per_page
                    
                    show_title = (page == 0)
                    page_tests = processed_tests[start:end]
                    fig = self._create_gantt_figure_for_export(
                        page_tests,
                        page + 1,
                        total_pages,
                        show_title,
                        show_labels_inside=self.show_labels_inside_checkbox.isChecked(),
                        total_duration=total_duration,
                        avg_duration=avg_duration
                    )
                    temp_path = os.path.join(temp_dir, f"temp_page_{page}.pdf")
                    try:
                        fig.write_image(
                            temp_path,
                            format='pdf',
                            width=1300,
                            height=max(400, len(page_tests) * 20)
                        )
                    except Exception as e:
                        print(f"Greška kod spremanja slike na stranici {page}: {e}")
                        raise
                    temp_pdf_paths.append(temp_path)
                self._show_export_progress(total_pages, total_pages)

                # Spoji PDF-ove
                merger = PdfMerger()
                for path in temp_pdf_paths:
                    merger.append(path)
                merger.write(output_path)
                
        finally:
            # Obriši privremeni folder
            if os.path.exists(temp_dir):
                for file in os.listdir(temp_dir):
                    os.remove(os.path.join(temp_dir, file))
                os.rmdir(temp_dir)
            if hasattr(self, 'progress_dialog'):
                self.progress_dialog.close()
                del self.progress_dialog

    def export_gantt_to_png_paginated(self):
        # ⬇⬇⬇ GLAVNA PROMJENA: koristi filtrirane testove umjesto svih
        filtered_tests = self.get_filtered_tests_for_export()
        
        if not filtered_tests:
            QtWidgets.QMessageBox.information(
                self, "Info", "Nema filtriranih testova za eksport."
            )
            return

        # Kreiraj privremeni folder
        temp_dir = os.path.join(os.path.dirname(__file__), "temp_png_export")
        os.makedirs(temp_dir, exist_ok=True)

        try:
            # ⬇⬇⬇ KORISTI filtered_tests umjesto self.testcase_rows
            tests = [t for t in filtered_tests if t['status'].strip().lower() != 'skipped']
            total_duration = sum(t['time'] for t in tests)  # ⬅ Preračunaj na temelju filtriranih
            avg_duration = total_duration / len(tests) if tests else 0

            # ⬇⬇⬇ NOVA FUNKCIJA ZA GRUPIRANJE PO SEKCIJAMA
            processed_tests = self._process_tests_with_sections(tests)
            
            if not processed_tests:
                return

            output_dir = QFileDialog.getExistingDirectory(self, "Odaberi direktorij za PNG export")
            if output_dir:
                tests_per_page = 30
                total_pages = (len(processed_tests) + tests_per_page - 1) // tests_per_page

                for page in range(total_pages):
                    self._show_export_progress(page, total_pages)
                    start = page * tests_per_page
                    end = start + tests_per_page
                    
                    show_title = (page == 0)
                    page_tests = processed_tests[start:end]
                    fig = self._create_gantt_figure_for_export(
                        page_tests,
                        page + 1,
                        total_pages,
                        show_title,
                        show_labels_inside=self.show_labels_inside_checkbox.isChecked(),
                        total_duration=total_duration,
                        avg_duration=avg_duration
                    )
                    output_file = os.path.join(
                        output_dir,
                        f"gantt_page_{page + 1}.png"
                    )
                    fig.write_image(
                        output_file,
                        format='png',
                        width=1300,
                        height=max(400, len(page_tests) * 35)
                    )
                self._show_export_progress(total_pages, total_pages)
                    
        finally:
            # Obriši privremeni folder
            if os.path.exists(temp_dir):
                for file in os.listdir(temp_dir):
                    os.remove(os.path.join(temp_dir, file))
                os.rmdir(temp_dir)
            if hasattr(self, 'progress_dialog'):
                self.progress_dialog.close()
                del self.progress_dialog

    # ⬇⬇⬇ NOVA FUNKCIJA KOJA DOBIVA FILTRIRANE TESTOVE
    def get_filtered_tests_for_export(self):
        if self.current_suite is not None and self.current_suite_tests:
            filtered_tests = self.current_suite_tests.copy()
            print(f"[DEBUG] Krenuo s {len(filtered_tests)} testova iz current_suite_tests")
        else:
            filtered_tests = self.testcase_rows.copy()
            print(f"[DEBUG] Krenuo s {len(filtered_tests)} testova iz testcase_rows")

        if hasattr(self, 'show_failed_only_checkbox') and self.show_failed_only_checkbox.isChecked():
            failed_suites = self.get_failed_test_suites()
            filtered_tests = [t for t in filtered_tests if t.get('section', '') in failed_suites]
            print(f"[DEBUG] Nakon 'show_failed_only': {len(filtered_tests)} testova")

        status_map = {
            '✅ passed': 'passed',
            '❌ failed': 'failed',
            '⚠️ skipped': 'skipped',
            '💥 error': 'error',
        }

        if hasattr(self, 'status_combo'):
            selected_status_ui = self.status_combo.currentText().strip()
            selected_status = status_map.get(selected_status_ui.lower(), None)
            if selected_status_ui.lower() in ['svi', 'prikaži sve']:
                # Ne filtriraj po statusu
                pass
            elif selected_status:
                filtered_tests = [t for t in filtered_tests if t['status'].strip().lower() == selected_status]
                print(f"[DEBUG] Nakon status filtera ({selected_status}): {len(filtered_tests)} testova")

        return filtered_tests

    # ⬇⬇⬇ POMOĆNA FUNKCIJA ZA DOHVAĆANJE FAILED TEST SUITES
    def get_failed_test_suites(self):
        """Vraća popis imena test suitova koji imaju barem jedan failed test"""
        failed_suites = set()
        
        for test in self.testcase_rows:
            if test['status'].strip().lower() == 'failed':
                suite_name = test.get('section', '')
                if suite_name:
                    failed_suites.add(suite_name)
        
        return failed_suites

    # ⬇⬇⬇ NOVA FUNKCIJA ZA PROCESIRANJE TESTOVA S SEKCIJAMA
    def _process_tests_with_sections(self, tests):
        """Grupira testove po imenu i sekciji, zadržava originalna vremena"""
        processed_tests = []
        name_section_counts = defaultdict(int)
        
        for test in tests:
            # Kreiraj jedinstveni ključ: ime + sekcija
            key = f"{test['name']}_{test.get('section', '')}"
            name_section_counts[key] += 1
            
            # Kreiraj kopiju testa s modificiranim imenom
            new_test = test.copy()
            section_part = f" ({test.get('section', 'N/A')})" if test.get('section') else ""
            new_test['display_name'] = f"{test['name']} #{name_section_counts[key]}{section_part}"
            
            processed_tests.append(new_test)
        
        return processed_tests

    # ⬇⬇⬇ MODIFICIRANA FUNKCIJA ZA EXPORT - koristi 'display_name'
    def _create_gantt_figure_for_export(self, test_list, page_num, total_pages, show_title=True, show_labels_inside=False, total_duration=None, avg_duration=None):
        """Kreira gantt figure za export - ne mijenja self.plotly_figure"""
        status_colors = {
            'Passed': 'rgb(0, 200, 0)',
            'Failed': 'rgb(255, 0, 0)',
            'Error': 'rgb(255, 165, 0)',
            'Skipped': 'rgb(160, 160, 160)'
        }
        
        bars = []
        
        if total_duration is None:
            total_duration = getattr(self, "total_execution_time", None)
            if total_duration is None:
                total_duration = sum(t['time'] for t in test_list)
        
        if avg_duration is None:
            avg_duration = total_duration / len(test_list) if test_list else 0

        for test in test_list:
            # ⬇⬇⬇ KORISTI display_name AKO POSTOJI, INAČE ORIGINALNO IME
            label = test.get('display_name', test['name'])
            bars.append({
                'Task': label,
                'Section': test.get('section', ''),
                'Status': test['status'],
                'Duration': test['time']  # ⬅ ZADRŽAVA ORIGINALNO VRIJEME!
            })

        fig = go.Figure()

        for bar in bars:
            if show_labels_inside:
                bar_text = f"{bar['Task']} ({timedelta(seconds=round(bar['Duration']))})"
            else:
                bar_text = f"{timedelta(seconds=round(bar['Duration']))}"

            fig.add_trace(go.Bar(
                x=[bar['Duration']],
                y=[bar['Task']],
                orientation='h',
                text=[bar_text],
                textposition='inside' if show_labels_inside else 'auto',
                insidetextanchor='middle',
                marker_color=status_colors.get(bar['Status']),
                textfont_color='black',
                showlegend=False
            ))

        layout_updates = {
            'yaxis': {
                'title': 'Testovi',
                'tickfont': {'color': 'black'},
                'automargin': True,
                'showticklabels': not show_labels_inside
            },
            'xaxis': {
                'title': 'Sekunde',
                'tickfont': {'color': 'black'},
                'zeroline': False
            },
            'plot_bgcolor': 'white',
            'height': max(400, len(bars) * 35),
            'showlegend': False
        }

        if show_title and page_num == 1:
            layout_updates['title'] = {
                'text': f"Gantt dijagram<br>Ukupno trajanje: {timedelta(seconds=round(total_duration))} | "
                    f"Prosječno: {timedelta(seconds=round(avg_duration))}",
                'x': 0.5,
                'xanchor': 'center',
                'font': {'color': 'black'}
            }

        fig.update_layout(**layout_updates)
        return fig

    def filter_testcases_by_search(self):
        self.last_search_text = self.search_input.text()
        filtered = self.filter_testcases()
        self.populate_testcase_table(filtered)
        show_labels = self.show_labels_inside_checkbox.isChecked()
        self.show_timeline_chart(filtered_tests=filtered, show_labels_inside=show_labels)

    def _split_tests_by_name(self, tests):
        """Grupira testove po imenu i dijeli trajanje proporcionalno"""
        grouped = defaultdict(list)
        for t in tests:
            grouped[t['name']].append(t)

        split_tests = []
        for name, items in grouped.items():
            count = len(items)
            for item in items:
                new_item = item.copy()
                new_item['time'] = item['time'] / count
                split_tests.append(new_item)

        return split_tests

    def _show_export_progress(self, current, total):
        """Prikazuje progress bar tijekom eksporta"""
        if not hasattr(self, 'progress_dialog'):
            self.progress_dialog = QProgressDialog("Eksport u tijeku...", "Prekini", 0, total, self)
            self.progress_dialog.setWindowTitle("Eksport")
            self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
            self.progress_dialog.setMinimumDuration(0)  # ⬅ Ovo je ključno – forsira prikaz odmah

        self.progress_dialog.setValue(current)
        QApplication.processEvents()  # Osigurava ažuriranje UI

        # ⚠️ Pozivaj ovu provjeru tek kad je eksport stvarno u tijeku (npr. current > 0)
        if current > 0 and self.progress_dialog.wasCanceled():
            raise Exception("Eksport prekinut")

    def filter_testcases(self):
        # Početni skup: svi testovi ili samo oni iz odabranog suitea
        if self.current_suite is None:
            base = self.testcase_rows
        else:
            base = [tc for tc in self.testcase_rows if tc["suite"] == self.current_suite]

        # Filtriraj po statusu
        if self.last_status_filter != "Prikaži sve":
            status_key = None
            if "Passed" in self.last_status_filter:
                status_key = "Passed"
            elif "Failed" in self.last_status_filter:
                status_key = "Failed"
            elif "Skipped" in self.last_status_filter:
                status_key = "Skipped"
            elif "Error" in self.last_status_filter:
                status_key = "Error"

            if status_key:
                base = [tc for tc in base if tc["status"] == status_key]

        # Filtriraj po search tekstu (case insensitive)
        if self.last_search_text:
            search_lower = self.last_search_text.lower()
            base = [tc for tc in base if search_lower in tc["name"].lower()]

        return base

    def analyze_selected_test(self):
        row = self.testcase_table.currentRow()

        if row < 0:
            QMessageBox.information(
                self,
                "Info",
                "Odaberi test u tablici."
            )
            return

        name = self.testcase_table.item(row, 0).text()

        test = next(
            (t for t in self.testcase_rows if t["name"] == name),
            None
        )

        if not test:
            return

        dialog = AIAnalysisDialog([test], self.total_tests, self)
        dialog.exec()
