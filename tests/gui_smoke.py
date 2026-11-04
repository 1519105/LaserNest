"""Kouřový test GUI bez displeje: QT_QPA_PLATFORM=offscreen python tests/gui_smoke.py out.png"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from lasernest import gui

EX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")


def main(out):
    app = QApplication([])
    app.setStyle("Fusion")
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    w = gui.MainWindow()
    w.resize(1400, 900)
    w.show()
    # tlačítko „Přidat…“ i položka menu musí otevřít dialog
    from PySide6.QtWidgets import QPushButton, QDialog
    seen = []
    def close_dialog():
        d = app.activeModalWidget()
        seen.append(type(d).__name__ if d else None)
        if d:
            d.reject()
    btn = [b for b in w.findChildren(QPushButton) if b.text().startswith("Přidat")][0]
    QTimer.singleShot(300, close_dialog); btn.click()
    act = [a for a in w.menuBar().actions()[0].menu().actions() if a.text().startswith("Přidat")][0]
    QTimer.singleShot(300, close_dialog); act.trigger()
    print("dialogy otevřeny:", seen)
    assert seen == ["AddPartDialog", "AddPartDialog"], seen
    w.add_part(os.path.join(EX, "sobik_2_rez.svg"), os.path.join(EX, "sobik_1_gravir.png"), count=6)
    w.add_part(os.path.join(EX, "stromecek_2_rez.svg"), os.path.join(EX, "stromecek_1_gravir.png"), count=8, fill=True)
    w.parts[1].fill = True
    loop = QEventLoop()
    w.run_nest()
    w._worker.done.connect(lambda *a: QTimer.singleShot(50, loop.quit))
    QTimer.singleShot(60000, loop.quit)
    loop.exec()
    n = len(w.items)
    w.table.selectRow(0)
    w.add_one()
    w.items[0].setSelected(True)
    w.rotate_selected(90)
    w.recheck()
    print("na desce:", n, "->", len(w.items), "| info:", w.info.text().replace("<br>", " | "))
    app.processEvents()
    w.grab().save(out)
    # projekt uložit / načíst
    pj = os.path.join(os.path.dirname(out), "test.lnest")
    w.project_path = pj
    w.save_project()
    w2 = gui.MainWindow()
    w2.open_project(pj)
    print("projekt znovu načten:", len(w2.items), "dílů")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "gui.png")
