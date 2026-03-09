
import sys
from PyQt6.QtWidgets import QApplication
from results_analysis import ResultsAnalysisWindow


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    win = ResultsAnalysisWindow()
    win.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
