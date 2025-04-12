# client_uploader.py
import sys, os, time, paramiko
from PyQt5 import QtWidgets
from PyQt5.QtWidgets import QFileDialog, QLabel, QPushButton

class Uploader(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.folder = ""
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("Updater Uploader")
        self.setGeometry(200, 200, 400, 150)

        self.label = QLabel("Выберите папку с bot.py", self)
        self.label.move(20, 20)

        btn_choose = QPushButton("Выбрать папку", self)
        btn_choose.move(20, 50)
        btn_choose.clicked.connect(self.select_folder)

        btn_upload = QPushButton("Загрузить обновление", self)
        btn_upload.move(150, 50)
        btn_upload.clicked.connect(self.upload_file)

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку")
        if folder:
            self.folder = folder
            self.label.setText(f"Выбрано: {os.path.basename(folder)}")

    def upload_file(self):
        if not self.folder:
            self.label.setText("Папка не выбрана!")
            return

        filepath = os.path.join(self.folder, "bot.py")
        if not os.path.exists(filepath):
            self.label.setText("Файл bot.py не найден!")
            return

        try:
            transport = paramiko.Transport(("194.156.66.56", 22))
            transport.connect(username="root", password="ТВОЙ_ПАРОЛЬ")
            sftp = paramiko.SFTPClient.from_transport(transport)
            sftp.put(filepath, "/root/updater/bot_update.py")  # временное имя

            sftp.close()
            transport.close()
            self.label.setText("Загружено успешно!")
        except Exception as e:
            self.label.setText(f"Ошибка: {e}")

app = QtWidgets.QApplication(sys.argv)
window = Uploader()
window.show()
sys.exit(app.exec_())
