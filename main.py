#!/usr/bin/env python3
"""
PyExplorer — Gestor de archivos para Linux en Python (PyQt5)

Objetivo: ofrecer una base sólida similar a Dolphin / Explorador de Windows:
- Vista de árbol (panel izquierdo) + vista de lista (panel central)
- Barra de herramientas con acciones: atrás, adelante, arriba, nueva pestaña, carpeta nueva, renombrar, eliminar (a Papelera), copiar, cortar, pegar, refrescar
- Barra de rutas (breadcrumbs) + cuadro de búsqueda
- Pestañas (como Windows/Dolphin)
- Menú contextual con "Abrir", "Abrir con aplicación predeterminada", "Abrir terminal aquí"
- Arrastrar y soltar entre paneles / pestañas
- Panel de previsualización (imágenes y texto básico)
- Diálogo de progreso para copiar/mover/pegar

Dependencias:
  - PyQt5  -> pip install PyQt5    (o apt: python3-pyqt5)
  - send2trash -> pip install send2trash   (para borrar seguro a la Papelera)

Ejecución:
  python3 pyexplorer.py

Nota: es un prototipo sólido y extensible. No implementa todas las funciones de Dolphin (dividir vista, protocolos KIO, etc.),
pero la arquitectura permite añadirlo fácilmente.
"""

import os
import sys
import shutil
import mimetypes
import threading
import subprocess
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt
from pathlib import Path
from send2trash import send2trash

APP_NAME = "PyExplorer"

# ---------------------- Utilidades ----------------------

def human_size(num):
    for unit in ['B','KB','MB','GB','TB']:
        if num < 1024.0:
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def open_with_default(path: Path):
    # Linux: xdg-open
    try:
        subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        print("Error abriendo con xdg-open:", e)


def open_terminal_here(directory: Path):
    # Usa el alternativo configurado en el sistema
    term = shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal") or shutil.which("konsole") or shutil.which("xterm")
    if term:
        try:
            subprocess.Popen([term], cwd=str(directory))
        except Exception as e:
            print("Error abriendo terminal:", e)


# ---------------------- Trabajos en segundo plano ----------------------
class FileOpWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(int, str)
    finished_ok = QtCore.pyqtSignal()
    finished_error = QtCore.pyqtSignal(str)

    def __init__(self, op, items, dest=None):
        super().__init__()
        self.op = op  # 'copy' o 'move'
        self.items = [Path(i) for i in items]
        self.dest = Path(dest) if dest else None

    def run(self):
        try:
            total_files = 0
            file_list = []
            for p in self.items:
                if p.is_dir():
                    for root, dirs, files in os.walk(p):
                        for f in files:
                            file_list.append(Path(root)/f)
                else:
                    file_list.append(p)
            total_files = max(len(file_list), 1)
            done = 0
            for p in self.items:
                target = self.dest / p.name if self.dest else None
                if self.op == 'copy':
                    if p.is_dir():
                        if target.exists():
                            # copiar dentro del existente
                            for root, dirs, files in os.walk(p):
                                rel = Path(root).relative_to(p)
                                (target/rel).mkdir(parents=True, exist_ok=True)
                                for f in files:
                                    srcf = Path(root)/f
                                    shutil.copy2(srcf, target/rel/f)
                                    done += 1
                                    self.progress.emit(int(done*100/total_files), str(srcf))
                        else:
                            shutil.copytree(p, target)
                            done += 1
                            self.progress.emit(int(done*100/total_files), str(p))
                    else:
                        shutil.copy2(p, target)
                        done += 1
                        self.progress.emit(int(done*100/total_files), str(p))
                elif self.op == 'move':
                    shutil.move(str(p), str(target))
                    done += 1
                    self.progress.emit(int(done*100/total_files), str(p))
            self.finished_ok.emit()
        except Exception as e:
            self.finished_error.emit(str(e))


# ---------------------- Widgets principales ----------------------
class Breadcrumbs(QtWidgets.QWidget):
    pathChanged = QtCore.pyqtSignal(Path)

    def __init__(self, path: Path):
        super().__init__()
        self.layout = QtWidgets.QHBoxLayout(self)
        self.layout.setContentsMargins(0,0,0,0)
        self.set_path(path)

    def clear_layout(self):
        while self.layout.count():
            item = self.layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def set_path(self, path: Path):
        self.clear_layout()
        parts = path.resolve().parts
        current = Path(parts[0])
        for i, part in enumerate(parts):
            if i == 0:
                current = Path(part)
            else:
                current = current / part
            btn = QtWidgets.QToolButton()
            btn.setText(part if part != os.sep else "/")
            btn.setAutoRaise(True)
            btn.clicked.connect(lambda checked=False, p=current: self.pathChanged.emit(p))
            self.layout.addWidget(btn)
        self.layout.addStretch(1)


class FileTab(QtWidgets.QWidget):
    requestOpenPath = QtCore.pyqtSignal(Path)
    statusMessage = QtCore.pyqtSignal(str)

    def __init__(self, start_path: Path):
        super().__init__()
        self.current_path = start_path.resolve()

        v = QtWidgets.QVBoxLayout(self)
        # Barra superior: breadcrumbs + búsqueda
        top = QtWidgets.QHBoxLayout()
        self.breadcrumbs = Breadcrumbs(self.current_path)
        self.breadcrumbs.pathChanged.connect(self.set_directory)
        top.addWidget(self.breadcrumbs, 1)

        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Buscar en esta carpeta… (Enter)")
        self.search_edit.returnPressed.connect(self.start_search)
        top.addWidget(self.search_edit)
        v.addLayout(top)

        # Splitter: árbol izquierda y lista derecha
        splitter = QtWidgets.QSplitter()
        splitter.setOrientation(Qt.Horizontal)
        v.addWidget(splitter, 1)

        # Árbol personalizado con iconos y agrupación
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMaximumWidth(220)
        splitter.addWidget(self.tree)

        icon_map = {
            "Carpeta Personal": QtGui.QIcon.fromTheme("user-home"),
            "Escritorio": QtGui.QIcon.fromTheme("user-desktop"),
            "Documentos": QtGui.QIcon.fromTheme("folder-documents"),
            "Música": QtGui.QIcon.fromTheme("folder-music"),
            "Imágenes": QtGui.QIcon.fromTheme("folder-pictures"),
            "Videos": QtGui.QIcon.fromTheme("folder-videos"),
            "Descargas": QtGui.QIcon.fromTheme("folder-download"),
            "Papelera": QtGui.QIcon.fromTheme("user-trash"),
            "Dispositivos": QtGui.QIcon.fromTheme("drive-harddisk"),
            "Red": QtGui.QIcon.fromTheme("network-workgroup"),
            "Ubicaciones": QtGui.QIcon.fromTheme("folder"),
        }

        home = str(Path.home())
        accesos = [
            ("Carpeta Personal", home),
            ("Escritorio", os.path.join(home, "Escritorio")),
            ("Documentos", os.path.join(home, "Documentos")),
            ("Música", os.path.join(home, "Música")),
            ("Imágenes", os.path.join(home, "Imágenes")),
            ("Videos", os.path.join(home, "Videos")),
            ("Descargas", os.path.join(home, "Descargas")),
            ("Papelera", "trash://"),
        ]

        # Nodo principal de ubicaciones
        ubicaciones = QtWidgets.QTreeWidgetItem(["Ubicaciones"])
        ubicaciones.setIcon(0, icon_map["Ubicaciones"])
        self.tree.addTopLevelItem(ubicaciones)

        for nombre, ruta in accesos:
            item = QtWidgets.QTreeWidgetItem([nombre])
            item.setIcon(0, icon_map.get(nombre, QtGui.QIcon()))
            item.setData(0, QtCore.Qt.UserRole, ruta)
            ubicaciones.addChild(item)

        # Nodo principal de dispositivos
        dispositivos = QtWidgets.QTreeWidgetItem(["Dispositivos"])
        dispositivos.setIcon(0, icon_map["Dispositivos"])
        dispositivos.setData(0, QtCore.Qt.UserRole, "/media")
        self.tree.addTopLevelItem(dispositivos)
        dispositivos.addChild(QtWidgets.QTreeWidgetItem(["Cargando..."]))  # hijo temporal

        # Nodo principal de red
        red = QtWidgets.QTreeWidgetItem(["Red"])
        red.setIcon(0, icon_map["Red"])
        red.setData(0, QtCore.Qt.UserRole, "/run/user/1000/gvfs")
        self.tree.addTopLevelItem(red)
        red.addChild(QtWidgets.QTreeWidgetItem(["Cargando..."]))  # hijo temporal

        self.tree.expandItem(ubicaciones)
        self.tree.itemClicked.connect(self.on_tree_item_clicked)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.on_tree_context_menu)
        self.tree.itemExpanded.connect(self.on_tree_item_expanded)

        # Lista
        self.file_model = QtWidgets.QFileSystemModel()
        self.file_model.setFilter(QtCore.QDir.AllEntries | QtCore.QDir.NoDotAndDotDot | QtCore.QDir.AllDirs)
        self.file_model.setRootPath(str(self.current_path))
        self.view = QtWidgets.QListView()
        self.view.setModel(self.file_model)
        self.view.setRootIndex(self.file_model.index(str(self.current_path)))
        self.view.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.view.doubleClicked.connect(self.on_double_click)
        self.view.setDragEnabled(True)
        self.view.setAcceptDrops(True)
        self.view.setDragDropMode(QtWidgets.QAbstractItemView.DragDrop)
        self.view.setDefaultDropAction(Qt.MoveAction)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self.context_menu)
        splitter.addWidget(self.view)

        # Ajustar tamaños del splitter sin previsualización
        splitter.setSizes([220, 600])

        # Acciones de copiar/pegar
        self.clipboard_paths = []
        self.clipboard_mode = None  # 'copy' o 'cut'

        # Atajos
        QtWidgets.QShortcut(QtGui.QKeySequence("Back"), self, activated=self.go_back)

        # Añadir historial
        self.history = [self.current_path]  # Inicializar con la ruta actual
        self.history_index = 0  # Comenzar en el primer elemento
        
        self.apply_settings()

        # Barra inferior con slider para tamaño de iconos
        bottom_bar = QtWidgets.QHBoxLayout()
        bottom_bar.addStretch(1)
        self.icon_size_slider = QtWidgets.QSlider(Qt.Horizontal)
        self.icon_size_slider.setMinimum(16)
        self.icon_size_slider.setMaximum(128)
        icon_size = int(QtCore.QSettings().value("icon_size", 48))
        self.icon_size_slider.setValue(icon_size)
        self.view.setIconSize(QtCore.QSize(icon_size, icon_size))
        if self.view.viewMode() == QtWidgets.QListView.IconMode:
            self.view.setGridSize(QtCore.QSize(icon_size + 32, icon_size + 32))
        self.icon_size_slider.setTickInterval(8)
        self.icon_size_slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
        self.icon_size_slider.setToolTip("Tamaño de iconos")
        self.icon_size_slider.valueChanged.connect(self.change_icon_size)
        bottom_bar.addWidget(QtWidgets.QLabel("Tamaño de iconos:"))
        bottom_bar.addWidget(self.icon_size_slider)
        v.addLayout(bottom_bar)

    # -------- Navegación --------
    def set_directory(self, path: Path):
        try:
            # Verificar si es un dispositivo montable
            if str(path).startswith('/dev/') or str(path).startswith('/media/'):
                if not os.access(path, os.R_OK):
                    # Intentar montar con permisos
                    if not self.mount_with_auth(path):
                        return
        
            if not os.access(path, os.R_OK):
                # Si no tenemos permiso de lectura, mostrar diálogo
                QtWidgets.QMessageBox.warning(
                    self,
                    "Error de permisos",
                    f"No tienes permisos para acceder a:\n{path}\n\nIntenta ejecutar el programa con privilegios elevados."
                )
                self.statusMessage.emit(f"Error de permisos: {path}")
                return
                
            if not path.exists():
                self.statusMessage.emit("Ruta no existe")
                return

            self.current_path = path.resolve()
            # Solo cambiar el modelo si no estamos en la papelera
            if not isinstance(self.view.model(), QtWidgets.QFileSystemModel):
                self.view.setModel(self.file_model)
            self.file_model.setRootPath(str(self.current_path))
            self.view.setRootIndex(self.file_model.index(str(self.current_path)))
            self.breadcrumbs.set_path(self.current_path)
            self.statusMessage.emit(str(self.current_path))

            # Actualizar historial
            if self.history_index < len(self.history) - 1:
                self.history = self.history[:self.history_index + 1]
            self.history.append(self.current_path)
            self.history_index = len(self.history) - 1
            
        except PermissionError as e:
            QtWidgets.QMessageBox.warning(
                self,
                "Error de permisos",
                f"No tienes permisos para acceder a:\n{path}\n\nIntenta ejecutar el programa con privilegios elevados."
            )
            self.statusMessage.emit(f"Error de permisos: {path}")
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self,
                "Error",
                f"Error al acceder a {path}:\n{str(e)}"
            )
            self.statusMessage.emit(f"Error: {str(e)}")

    def on_tree_item_clicked(self, item, column):
        path = item.data(0, QtCore.Qt.UserRole)
        if path == "trash://":
            self.show_trash_view()
            return
        self.set_directory(Path(path))

    def on_tree_item_expanded(self, item):
        """Se llama cuando se expande un elemento del árbol"""
        path = item.data(0, QtCore.Qt.UserRole)
        if path == "/media":
            self.refresh_devices(item)
        elif path == "/run/user/1000/gvfs":
            item.takeChildren()
            gvfs_path = Path("/run/user/1000/gvfs")
            if gvfs_path.exists():
                for net in gvfs_path.iterdir():
                    net_item = QtWidgets.QTreeWidgetItem([net.name])
                    net_item.setIcon(0, QtGui.QIcon.fromTheme("network-server"))
                    net_item.setData(0, QtCore.Qt.UserRole, str(net))
                    item.addChild(net_item)
            if item.childCount() == 0:
                item.addChild(QtWidgets.QTreeWidgetItem(["(Sin recursos de red)"]))

    def show_trash_view(self):
        trash_path = Path.home() / ".local/share/Trash/files"
        info_path = Path.home() / ".local/share/Trash/info"
        if not trash_path.exists():
            self.statusMessage.emit("La Papelera está vacía.")
            empty_model = QtGui.QStandardItemModel()
            empty_model.setHorizontalHeaderLabels(["Nombre", "Ruta"])
            self.view.setModel(empty_model)
            return

        icon_provider = QtWidgets.QFileIconProvider()
        model = QtGui.QStandardItemModel()
        model.setHorizontalHeaderLabels(["Nombre", "Ruta"])
        for f in trash_path.iterdir():
            item_name = QtGui.QStandardItem(f.name)
            item_path = QtGui.QStandardItem(str(f))
            # Asignar icono según tipo
            if f.is_dir():
                icon = icon_provider.icon(QtWidgets.QFileIconProvider.Folder)
            else:
                icon = icon_provider.icon(QtCore.QFileInfo(str(f)))
            item_name.setIcon(icon)
            model.appendRow([item_name, item_path])
        self.view.setModel(model)
        self.statusMessage.emit(f"Papelera: {trash_path} ({model.rowCount()} elemento(s))")

        # Menú contextual para restaurar
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.disconnect()
        self.view.customContextMenuRequested.connect(lambda pos: self.trash_context_menu(pos, model))

        # Permitir doble clic para abrir el archivo
        self.view.doubleClicked.disconnect()
        self.view.doubleClicked.connect(lambda idx: open_with_default(Path(model.item(idx.row(), 1).text())))

    def trash_context_menu(self, pos, model):
        idx = self.view.indexAt(pos)
        if not idx.isValid():
            return
        file_path = Path(model.item(idx.row(), 1).text())
        menu = QtWidgets.QMenu(self.view)
        act_open = menu.addAction("Abrir")
        act_restore = menu.addAction("Restaurar")
        act_delete = menu.addAction("Eliminar definitivamente")
        action = menu.exec_(self.view.viewport().mapToGlobal(pos))
        if action == act_open:
            open_with_default(file_path)
        elif action == act_restore:
            self.restore_from_trash(file_path)
        elif action == act_delete:
            self.delete_permanently(file_path)

    def restore_from_trash(self, file_path: Path):
        info_path = Path.home() / ".local/share/Trash/info" / (file_path.name + ".trashinfo")
        if not info_path.exists():
            QtWidgets.QMessageBox.warning(self, "Restaurar", "No se encontró información de restauración.")
            return
        # Leer la ruta original del archivo
        with open(info_path, "r") as f:
            for line in f:
                if line.startswith("Path="):
                    orig_path = line[len("Path="):].strip()
                    break
            else:
                QtWidgets.QMessageBox.warning(self, "Restaurar", "No se encontró la ruta original.")
                return
        try:
            orig = Path(orig_path)
            # Si existe, preguntar si sobrescribir
            if orig.exists():
                ans = QtWidgets.QMessageBox.question(self, "Restaurar", f"El archivo original existe:\n{orig}\n¿Sobrescribir?", QtWidgets.QMessageBox.Yes|QtWidgets.QMessageBox.No)
                if ans != QtWidgets.QMessageBox.Yes:
                    return
            shutil.move(str(file_path), str(orig))
            info_path.unlink()
            self.show_trash_view()
            QtWidgets.QMessageBox.information(self, "Restaurar", f"Archivo restaurado a:\n{orig}")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Restaurar", f"No se pudo restaurar:\n{e}")

    def delete_permanently(self, file_path: Path):
        info_path = Path.home() / ".local/share/Trash/info" / (file_path.name + ".trashinfo")
        try:
            if file_path.is_dir():
                shutil.rmtree(file_path)
            else:
                file_path.unlink()
            if info_path.exists():
                info_path.unlink()
            self.show_trash_view()
            QtWidgets.QMessageBox.information(self, "Eliminar", "Archivo eliminado definitivamente.")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Eliminar", f"No se pudo eliminar:\n{e}")

    def on_double_click(self, index):
        p = Path(self.file_model.filePath(index))
        if p.is_dir():
            self.set_directory(p)
        else:
            open_with_default(p)

    def go_up(self):
        self.set_directory(self.current_path.parent)

    def go_back(self):
        if self.history_index > 0:
            self.history_index -= 1
            path = self.history[self.history_index]
            # Evitar llamar a set_directory para no modificar el historial
            self.current_path = path.resolve()
            self.file_model.setRootPath(str(self.current_path))
            self.view.setRootIndex(self.file_model.index(str(self.current_path)))
            self.breadcrumbs.set_path(self.current_path)
            self.statusMessage.emit(str(self.current_path))
            return True
        return False

    def go_forward(self):
        if self.history_index < len(self.history) - 1:
            self.history_index += 1
            path = self.history[self.history_index]
            # Evitar llamar a set_directory para no modificar el historial
            self.current_path = path.resolve()
            self.file_model.setRootPath(str(self.current_path))
            self.view.setRootIndex(self.file_model.index(str(self.current_path)))
            self.breadcrumbs.set_path(self.current_path)
            self.statusMessage.emit(str(self.current_path))
            return True
        return False

    def refresh(self):
        self.set_directory(self.current_path)

    def apply_settings(self):
        """Aplica las configuraciones guardadas a la pestaña actual"""
        settings = QtCore.QSettings()
        show_hidden = settings.value("show_hidden", False, bool)
        view_mode = settings.value("view_mode", "Lista")
        auto_exec = settings.value("auto_exec", False, bool)
        
        # Aplicar filtros según configuración
        filters = QtCore.QDir.AllEntries | QtCore.QDir.NoDotAndDotDot
        if show_hidden:
            filters |= QtCore.QDir.Hidden
            
        # Aplicar filtro solo al modelo de archivos
        self.file_model.setFilter(filters | QtCore.QDir.AllDirs)
        
        # Aplicar modo de vista
        if view_mode == "Iconos":
            self.view.setViewMode(QtWidgets.QListView.IconMode)
            self.view.setIconSize(QtCore.QSize(48, 48))
            self.view.setGridSize(QtCore.QSize(80, 80))
            self.view.setSpacing(10)
        elif view_mode == "Iconos grandes":
            self.view.setViewMode(QtWidgets.QListView.IconMode)
            self.view.setIconSize(QtCore.QSize(96, 96))
            self.view.setGridSize(QtCore.QSize(120, 120))
            self.view.setSpacing(20)
        else:  # Lista
            self.view.setViewMode(QtWidgets.QListView.ListMode)
            self.view.setIconSize(QtCore.QSize(16, 16))
            self.view.setGridSize(QtCore.QSize())
            self.view.setSpacing(0)
        
        # Aplicar permisos automáticos si está habilitado
        if auto_exec:
            self.apply_exec_permissions()
        
        # Actualizar vista
        self.refresh()

    def apply_exec_permissions(self):
        """Aplica permisos de ejecución a archivos con extensiones especificadas"""
        settings = QtCore.QSettings()
        extensions = settings.value("exec_extensions", "sh py bash pl rb").split()
        
        try:
            for p in self.current_path.iterdir():
                if p.is_file() and p.suffix.lstrip('.') in extensions:
                    current = p.stat().st_mode
                    p.chmod(current | 0o111)  # Agregar permisos de ejecución
        except Exception as e:
            self.statusMessage.emit(f"Error al cambiar permisos: {e}")
    
    def change_icon_size(self, value):
        """Cambia el tamaño de los iconos en la vista de archivos."""
        self.view.setIconSize(QtCore.QSize(value, value))
        if self.view.viewMode() == QtWidgets.QListView.IconMode:
            # Ajusta también la grilla para que los iconos no se superpongan
            self.view.setGridSize(QtCore.QSize(value + 32, value + 32))

        # Guardar en configuración
        settings = QtCore.QSettings()
        settings.setValue("icon_size", value)


    # -------- Búsqueda --------
    def start_search(self):
        term = self.search_edit.text().strip()
        if not term:
            return
        self.statusMessage.emit(f"Buscando '{term}' en {self.current_path}…")
        results = []
        for root, dirs, files in os.walk(self.current_path):
            for name in files + dirs:
                if term.lower() in name.lower():
                    results.append(str(Path(root)/name))
    
        if results:
            self.statusMessage.emit(f"Encontrados {len(results)} resultado(s)")
        else:
            self.statusMessage.emit("0 resultados")

    # -------- Portapapeles (copiar/cortar/pegar) --------
    def selected_paths(self):
        sel = self.view.selectionModel().selectedIndexes()
        return [Path(self.file_model.filePath(i)) for i in sel]

    def copy_selected(self):
        self.clipboard_paths = self.selected_paths()
        self.clipboard_mode = 'copy'
        self.statusMessage.emit(f"Copiados {len(self.clipboard_paths)} elemento(s)")

    def cut_selected(self):
        self.clipboard_paths = self.selected_paths()
        self.clipboard_mode = 'cut'
        self.statusMessage.emit(f"Cortados {len(self.clipboard_paths)} elemento(s)")

    def paste_into_current(self):
        if not self.clipboard_paths:
            return
        op = 'move' if self.clipboard_mode == 'cut' else 'copy'
        worker = FileOpWorker(op, [str(p) for p in self.clipboard_paths], dest=str(self.current_path))
        dlg = QtWidgets.QProgressDialog(f"{op.title()}…", "Cancelar", 0, 100, self)
        dlg.setWindowTitle("Progreso")
        dlg.setAutoClose(True)
        worker.progress.connect(lambda pct, f: dlg.setValue(pct))
        worker.finished_ok.connect(lambda: (dlg.setValue(100), self.refresh()))
        worker.finished_error.connect(lambda err: QtWidgets.QMessageBox.critical(self, "Error", err))
        worker.start()
        dlg.exec_()
        self.clipboard_paths = []
        self.clipboard_mode = None

    # -------- Archivos --------
    def new_folder(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "Carpeta nueva", "Nombre:")
        if ok and name:
            p = self.current_path / name
            try:
                p.mkdir(parents=False, exist_ok=False)
                self.refresh()
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Error", str(e))

    def rename_selected(self):
        paths = self.selected_paths()
        if len(paths) != 1:
            QtWidgets.QMessageBox.information(self, "Renombrar", "Selecciona un único elemento")
            return
        p = paths[0]
        name, ok = QtWidgets.QInputDialog.getText(self, "Renombrar", "Nuevo nombre:", text=p.name)
        if ok and name and name != p.name:
            try:
                p.rename(p.parent / name)
                self.refresh()
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Error", str(e))

    def delete_selected(self):
        paths = self.selected_paths()
        if not paths:
            return
        ans = QtWidgets.QMessageBox.question(self, "Eliminar", f"Enviar {len(paths)} elemento(s) a la Papelera?", QtWidgets.QMessageBox.Yes|QtWidgets.QMessageBox.No)
        if ans == QtWidgets.QMessageBox.Yes:
            for p in paths:
                try:
                    send2trash(str(p))
                except Exception as e:
                    QtWidgets.QMessageBox.warning(self, "Error", f"No se pudo enviar a Papelera: {p}\n{e}")
            self.refresh()

    # -------- Menú contextual --------
    def context_menu(self, pos):
        menu = QtWidgets.QMenu(self)
        idx = self.view.indexAt(pos)
        path = Path(self.file_model.filePath(idx)) if idx.isValid() else self.current_path

        act_run = None  # <--- Añade esta línea

        if idx.isValid():
            act_open = menu.addAction("Abrir")
            act_open_with = menu.addAction("Abrir con...")
            
            # Agregar opción de ejecutar para archivos .sh
            if path.suffix == ".sh" or os.access(str(path), os.X_OK):
                act_run = menu.addAction("Ejecutar en terminal")
        
            menu.addSeparator()
            act_copy = menu.addAction("Copiar")
            act_cut = menu.addAction("Cortar") 
            act_paste = menu.addAction("Pegar")
            menu.addSeparator()
            act_rename = menu.addAction("Renombrar")
            act_delete = menu.addAction("Eliminar (Papelera)")
            # Agregar nuevas opciones
            menu.addSeparator()
            act_move_to = menu.addAction("Mover a...")
            act_properties = menu.addAction("Propiedades")
            menu.addSeparator()
            act_terminal = menu.addAction("Abrir terminal aquí")

            action = menu.exec_(self.view.viewport().mapToGlobal(pos))
            if not action:
                return
            
            if act_run and action == act_run:  # <--- Cambia aquí
                self.run_in_terminal(path)
            elif action == act_open:
                if path.is_dir():
                    self.set_directory(path)
                else:
                    open_with_default(path)
            elif action == act_open_with:
                self.open_with_dialog(path)
            elif action == act_copy:
                self.copy_selected()
            elif action == act_cut:
                self.cut_selected()
            elif action == act_paste:
                self.paste_into_current()
            elif action == act_rename:
                self.rename_selected()
            elif action == act_delete:
                self.delete_selected()
            # Agregar handlers para las nuevas opciones
            elif action == act_move_to:
                self.move_to_dialog()
            elif action == act_properties:
                self.show_properties_dialog(path)
            elif action == act_terminal:
                open_terminal_here(self.current_path)

    def move_to_dialog(self):
        """Abre un diálogo para mover archivos seleccionados a otra ubicación"""
        paths = self.selected_paths()
        if not paths:
            QtWidgets.QMessageBox.information(self, "Mover a...", "Selecciona al menos un archivo o carpeta.")
            return
            
        dest = QtWidgets.QFileDialog.getExistingDirectory(self, "Seleccionar carpeta destino", str(self.current_path))
        if dest:
            try:
                for p in paths:
                    shutil.move(str(p), os.path.join(dest, p.name))
                self.refresh()
                self.statusMessage.emit(f"Movidos {len(paths)} elemento(s) a {dest}")
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Error al mover", str(e))

    def show_properties_dialog(self, path: Path):
        """Muestra un diálogo con las propiedades del archivo/carpeta"""
        info = QtCore.QFileInfo(str(path))
        size = human_size(info.size()) if info.isFile() else "-"
        tipo = "Carpeta" if info.isDir() else "Archivo"
        modificado = info.lastModified().toString("yyyy-MM-dd hh:mm:ss")
        creado = info.created().toString("yyyy-MM-dd hh:mm:ss")
        permisos = info.permissions()
        permisos_str = ""
        if permisos & QtCore.QFile.ReadOwner: permisos_str += "r"
        if permisos & QtCore.QFile.WriteOwner: permisos_str += "w"
        if permisos & QtCore.QFile.ExeOwner: permisos_str += "x"

        msg = (
            f"<b>Nombre:</b> {info.fileName()}<br>"
            f"<b>Ruta:</b> {info.absoluteFilePath()}<br>"
            f"<b>Tipo:</b> {tipo}<br>"
            f"<b>Tamaño:</b> {size}<br>"
            f"<b>Modificado:</b> {modificado}<br>"
            f"<b>Creado:</b> {creado}<br>"
            f"<b>Permisos:</b> {permisos_str}"
        )
        QtWidgets.QMessageBox.information(self, "Propiedades", msg)

    def on_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        path = item.data(0, QtCore.Qt.UserRole)
        nombre = item.text(0)

        menu = QtWidgets.QMenu(self.tree)
        if path == "/media":
            # Menú para el nodo principal de dispositivos
            act_refresh = menu.addAction("Actualizar dispositivos")
            action = menu.exec_(self.tree.viewport().mapToGlobal(pos))
            if not action:
                return
            if action == act_refresh:
                self.refresh_devices(item)
        elif path.startswith("/dev/"):  # Modificado para incluir dispositivos /dev/
            # Menú para dispositivos individuales
            act_open = menu.addAction("Abrir")
            act_tab = menu.addAction("Abrir en una pestaña nueva")
            menu.addSeparator()
            
            # Verificar si está montado
            is_mounted = self.is_device_mounted(Path(path))
            if is_mounted:
                act_unmount = menu.addAction("Desmontar")
                act_unmount.setIcon(QtGui.QIcon.fromTheme("media-eject"))
            else:
                act_mount = menu.addAction("Montar")
                act_mount.setIcon(QtGui.QIcon.fromTheme("media-mount"))
        
            action = menu.exec_(self.tree.viewport().mapToGlobal(pos))
            if not action:
                return
            
            if action == act_open:
                if is_mounted:
                    self.set_directory(Path(path))
                else:
                    if self.mount_device(Path(path)):
                        self.set_directory(Path(path))
            elif action == act_tab:
                if is_mounted:
                    self.requestOpenPath.emit(Path(path))
                else:
                    if self.mount_device(Path(path)):
                        self.requestOpenPath.emit(Path(path))
            elif is_mounted and action == act_unmount:
                self.unmount_device(Path(path))
                self.refresh_devices(item.parent())
            elif not is_mounted and action == act_mount:
                if self.mount_device(Path(path)):
                    self.refresh_devices(item.parent())
                    self.statusMessage.emit(f"Dispositivo montado: {path}")
        else:
            # Menú normal para otros items
            act_open = menu.addAction("Abrir")
            act_tab = menu.addAction("Abrir en una pestaña nueva")
            act_win = menu.addAction("Abrir en una ventana nueva")
            action = menu.exec_(self.tree.viewport().mapToGlobal(pos))
            if not action:
                return
            if action == act_open:
                self.set_directory(Path(path))
            elif action == act_tab:
                self.requestOpenPath.emit(Path(path))
            elif action == act_win:
                subprocess.Popen([sys.executable, sys.argv[0], str(path)])

    def refresh_devices(self, item):
        """Actualiza la lista de dispositivos"""
        item.takeChildren()
        
        try:
            # Obtener información de los dispositivos usando lsblk
            result = subprocess.run(
                ['lsblk', '-o', 'NAME,SIZE,LABEL,MOUNTPOINT', '--json'],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                import json
                devices = json.loads(result.stdout)
                
                for device in devices.get('blockdevices', []):
                    if device['name'].startswith('sd'):
                        # Crear item para el dispositivo
                        dev_name = f"/dev/{device['name']}"
                        label = device.get('label', '')
                        size = device.get('size', '')
                        mountpoint = device.get('mountpoint', '')
                        
                        display_name = dev_name
                        if label:
                            display_name += f" ({label})"
                        if size:
                            display_name += f" - {size}"
                        if mountpoint:
                            display_name += f" [Montado en {mountpoint}]"
                        
                        dev_item = QtWidgets.QTreeWidgetItem([display_name])
                        dev_item.setIcon(0, QtGui.QIcon.fromTheme("drive-harddisk"))
                        
                        # Guardar la ruta del dispositivo o punto de montaje
                        if mountpoint:
                            dev_item.setData(0, QtCore.Qt.UserRole, mountpoint)
                        else:
                            dev_item.setData(0, QtCore.Qt.UserRole, dev_name)
                        
                        item.addChild(dev_item)
                        
                        # Añadir particiones si existen
                        if 'children' in device:
                            for child in device['children']:
                                child_name = f"/dev/{child['name']}"
                                child_label = child.get('label', '')
                                child_size = child.get('size', '')
                                child_mount = child.get('mountpoint', '')
                                
                                child_display = child_name
                                if child_label:
                                    child_display += f" ({child_label})"
                                if child_size:
                                    child_display += f" - {child_size}"
                                if child_mount:
                                    child_display += f" [Montado en {child_mount}]"
                                
                                child_item = QtWidgets.QTreeWidgetItem([child_display])
                                child_item.setIcon(0, QtGui.QIcon.fromTheme("drive-harddisk"))
                                
                                if child_mount:
                                    child_item.setData(0, QtCore.Qt.UserRole, child_mount)
                                else:
                                    child_item.setData(0, QtCore.Qt.UserRole, child_name)
                                
                                dev_item.addChild(child_item)
            
            # Si no hay dispositivos, mostrar mensaje
            if item.childCount() == 0:
                item.addChild(QtWidgets.QTreeWidgetItem(["(Sin dispositivos)"]))
                
        except Exception as e:
            print(f"Error al refrescar dispositivos: {e}")
            item.addChild(QtWidgets.QTreeWidgetItem([f"Error: {str(e)}"]))
            return  # Agregar return para evitar continuar después del error

    def is_device_mounted(self, device_path: Path) -> bool:
        """Verifica si un dispositivo está montado"""
        try:
            with open('/proc/mounts', 'r') as f:
                mounts = f.readlines()
                for mount in mounts:
                    if str(device_path) in mount:
                        return True
            return False
        except Exception:
            return False

    def mount_device(self, device_path: Path) -> bool:
        """Monta un dispositivo usando pkexec y udisksctl"""
        try:
            # Crear un script temporal con los comandos necesarios
            mount_script = Path('/tmp/mount_script.sh')
            with open(mount_script, 'w') as f:
                f.write('#!/bin/bash\n')
                f.write(f'udisksctl mount -b {device_path}\n')
        
            # Dar permisos de ejecución al script
            mount_script.chmod(0o755)
        
            # Ejecutar con pkexec para solicitar autorización
            result = subprocess.run(
                ['pkexec', str(mount_script)], 
                capture_output=True,
                text=True
            )
        
            # Eliminar el script temporal
            mount_script.unlink()
        
            if result.returncode == 0:
                self.statusMessage.emit(f"Dispositivo montado: {device_path}")
                return True
            else:
                QtWidgets.QMessageBox.warning(
                    self, 
                    "Error al montar",
                    f"No se pudo montar {device_path}:\n{result.stderr}"
                )
                return False
            
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self,
                "Error al montar",
                f"Error: {str(e)}"
            )
            return False

    def unmount_device(self, device_path: Path):
        """Desmonta un dispositivo usando pkexec y udisksctl"""
        try:
            # Crear script temporal
            unmount_script = Path('/tmp/unmount_script.sh')
            with open(unmount_script, 'w') as f:
                f.write('#!/bin/bash\n')
                f.write(f'udisksctl unmount -b {device_path}\n')
        
            # Dar permisos de ejecución
            unmount_script.chmod(0o755)
        
            # Ejecutar con pkexec para solicitar autorización
            result = subprocess.run(
                ['pkexec', str(unmount_script)],
                capture_output=True,
                text=True
            )
        
            # Eliminar script temporal
            unmount_script.unlink()
        
            if result.returncode == 0:
                self.statusMessage.emit(f"Dispositivo desmontado: {device_path}")
            else:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Error al desmontar", 
                    f"No se pudo desmontar {device_path}:\n{result.stderr}"
                )
            
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self,
                "Error al desmontar",
                f"Error: {str(e)}"
            )

    def mount_with_auth(self, device_path: Path) -> bool:
        """Muestra diálogo de autenticación y monta el dispositivo"""
        try:
            # Crear diálogo personalizado
            auth_dialog = QtWidgets.QDialog(self)
            auth_dialog.setWindowTitle("Autenticación requerida")
            auth_dialog.setModal(True)
            auth_dialog.setMinimumWidth(400)
            
            layout = QtWidgets.QVBoxLayout(auth_dialog)
            
            # Icono y mensaje
            header = QtWidgets.QHBoxLayout()
            icon_label = QtWidgets.QLabel()
            icon_label.setPixmap(QtGui.QIcon.fromTheme("dialog-password").pixmap(48, 48))
            header.addWidget(icon_label)
            
            msg_label = QtWidgets.QLabel(
                f"Se requiere autenticación para acceder al dispositivo:\n"
                f"{device_path}\n\n"
                "Este dispositivo requiere permisos de administrador para acceder."
            )
            msg_label.setWordWrap(True)
            header.addWidget(msg_label)
            layout.addLayout(header)
            
            # Botones
            buttons = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.Yes | 
                QtWidgets.QDialogButtonBox.No
            )
            buttons.button(QtWidgets.QDialogButtonBox.Yes).setText("Dar permisos")
            buttons.button(QtWidgets.QDialogButtonBox.No).setText("Cancelar")
            layout.addWidget(buttons)
            
            buttons.accepted.connect(auth_dialog.accept)
            buttons.rejected.connect(auth_dialog.reject)

            # Mostrar diálogo
            if auth_dialog.exec_() == QtWidgets.QDialog.Accepted:
                # Usuario aceptó dar permisos
                try:
                    # Crear script temporal para montar con permisos
                    mount_script = Path('/tmp/mount_script.sh')
                    with open(mount_script, 'w') as f:
                        f.write('#!/bin/bash\n')
                        f.write(f'udisksctl mount -b {device_path}\n')
                        f.write(f'chmod 777 "$(udisksctl mount -b {device_path} | cut -d" " -f4)"\n')
                    
                    # Dar permisos de ejecución al script
                    mount_script.chmod(0o755)
                    
                    # Ejecutar con pkexec
                    result = subprocess.run(
                        ['pkexec', str(mount_script)],
                        capture_output=True,
                        text=True
                    )
                    
                    # Limpiar script temporal
                    mount_script.unlink()
                    
                    if result.returncode == 0:
                        self.statusMessage.emit(f"Dispositivo montado con permisos: {device_path}")
                        return True
                    else:
                        QtWidgets.QMessageBox.warning(
                            self,
                            "Error al montar",
                            f"No se pudo montar el dispositivo:\n{result.stderr}"
                        )
                        return False
                        
                except Exception as e:
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Error",
                        f"Error al montar el dispositivo:\n{str(e)}"
                    )
                    return False
            return False
            
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self,
                "Error",
                f"Error al mostrar diálogo de autenticación:\n{str(e)}"
            )
            return False

    def run_in_terminal(self, path: Path):
        """Ejecuta un archivo script en una nueva terminal"""
        try:
            # Asegurarse que el archivo tenga permisos de ejecución
            if not os.access(str(path), os.X_OK):
                current = path.stat().st_mode
                path.chmod(current | 0o111)  # Agregar permisos de ejecución
                
            # Encontrar un emulador de terminal disponible
            term = (shutil.which("x-terminal-emulator") or 
                    shutil.which("gnome-terminal") or 
                    shutil.which("konsole") or 
                    shutil.which("xterm"))
                    
            if term:
                # Ejecutar el script en una nueva terminal y mantener la terminal abierta
                subprocess.Popen([
                    term, 
                    "-e", 
                    f"bash -c '{str(path)}; echo; read -p \"Presione Enter para cerrar...\"'"
                ])
            else:
                QtWidgets.QMessageBox.critical(
                    self, 
                    "Error", 
                    "No se encontró un emulador de terminal."
                )
                
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, 
                "Error", 
                f"No se pudo ejecutar el script:\n{e}"
            )

    def open_with_dialog(self, path: Path):
        """Muestra un diálogo para elegir con qué aplicación abrir el archivo"""
        # Obtener el tipo MIME del archivo
        mime_type = subprocess.run(
            ['xdg-mime', 'query', 'filetype', str(path)],
            capture_output=True,
            text=True
        ).stdout.strip()

        # Buscar archivos .desktop en ubicaciones estándar
        desktop_dirs = [
            Path.home() / ".local/share/applications",
            Path("/usr/share/applications"),
            Path("/usr/local/share/applications"),
        ]
        apps = []
        for ddir in desktop_dirs:
            if ddir.exists():
                for f in ddir.glob("*.desktop"):
                    try:
                        with open(f, "r", encoding="utf-8", errors="ignore") as desk:
                            content = desk.read()
                            if f"MimeType={mime_type}" in content or f"MimeType=" in content:
                                name = None
                                exec_cmd = None
                                icon = None
                                for line in content.splitlines():
                                    if line.startswith("Name="):
                                        name = line.split("=", 1)[1]
                                    elif line.startswith("Exec="):
                                        exec_cmd = line.split("=", 1)[1]
                                    elif line.startswith("Icon="):
                                        icon = line.split("=", 1)[1]
                                if name and exec_cmd:
                                    apps.append((name, exec_cmd, icon))
                    except Exception:
                        continue

        # Crear diálogo
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Abrir con...")
        layout = QtWidgets.QVBoxLayout(dialog)
        listw = QtWidgets.QListWidget()
        for name, exec_cmd, icon in apps:
            item = QtWidgets.QListWidgetItem(name)
            if icon:
                item.setIcon(QtGui.QIcon.fromTheme(icon))
            item.setData(Qt.UserRole, exec_cmd)
            listw.addItem(item)
        layout.addWidget(QtWidgets.QLabel("Selecciona una aplicación:"))
        layout.addWidget(listw)

        # Botón para buscar otra aplicación
        browse_btn = QtWidgets.QPushButton("Buscar otra aplicación...")
        layout.addWidget(browse_btn)

        # Botones OK/Cancel
        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        layout.addWidget(button_box)

        def browse_app():
            file_name, _ = QtWidgets.QFileDialog.getOpenFileName(
                dialog,
                "Seleccionar aplicación",
                "/usr/bin",
                "Todos los archivos (*)"
            )
            if file_name:
                item = QtWidgets.QListWidgetItem(file_name)
                item.setData(Qt.UserRole, file_name)
                listw.addItem(item)
                listw.setCurrentItem(item)

        browse_btn.clicked.connect(browse_app)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)

        if dialog.exec_() == QtWidgets.QDialog.Accepted and listw.currentItem():
            cmd = listw.currentItem().data(Qt.UserRole)
            # Reemplazar %f o %F con la ruta del archivo
            cmd = cmd.replace('%f', str(path)).replace('%F', str(path))
            # Eliminar otros parámetros %x
            cmd = ' '.join(x for x in cmd.split() if not x.startswith('%'))
            try:
                subprocess.Popen(cmd.split())
            except Exception as e:
                QtWidgets.QMessageBox.critical(
                    self,
                    "Error",
                    f"No se pudo abrir el archivo:\n{e}"
                )


# ---------------------- Ventana principal ----------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QtGui.QIcon(str(Path(__file__).parent / "icons/icon.png")))  # <-- Icono de la app
        
        # Restaurar geometría de la ventana
        settings = QtCore.QSettings()
        geometry = settings.value("window_geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            # Tamaño por defecto si no hay geometría guardada
            self.resize(1100, 700)
            
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.setCentralWidget(self.tabs)

        # Barra de herramientas
        tb = self.addToolBar("Main")
        tb.setIconSize(QtCore.QSize(20,20))
        style = QtWidgets.QApplication.style()
        self.act_back = tb.addAction(QtGui.QIcon("icons/atras.png"), "Atrás")
        self.act_up = tb.addAction(QtGui.QIcon("icons/arriba.png"), "Adelante")
        self.act_forward = tb.addAction(QtGui.QIcon("icons/adelante.png"), "Adelante")
        tb.addSeparator()

        self.address = QtWidgets.QLineEdit()
        self.address.setPlaceholderText("Ruta… Enter para ir")
        self.address.returnPressed.connect(self.go_address)
        addrw = QtWidgets.QWidget()
        addr_layout = QtWidgets.QHBoxLayout(addrw)
        addr_layout.setContentsMargins(0,0,0,0)
        addr_layout.addWidget(self.address)
        tb.addWidget(addrw)

        tb.addSeparator()
        self.act_newtab = tb.addAction(QtGui.QIcon("icons/new_tab.png"), "Nueva pestaña")
        self.act_newfolder = tb.addAction(QtGui.QIcon("icons/new_folde.png"), "Carpeta nueva")
        self.act_rename = tb.addAction(QtGui.QIcon("icons/escribir.png"), "Renombrar")
        self.act_delete = tb.addAction(QtGui.QIcon("icons/delete.png"), "Eliminar")
        tb.addSeparator()
        self.act_copy = tb.addAction(QtGui.QIcon("icons/copiar.png"), "Copiar")
        self.act_cut = tb.addAction(QtGui.QIcon("icons/cortar.png"), "Cortar")
        self.act_paste = tb.addAction(QtGui.QIcon("icons/pegar.png"), "Pegar")
        tb.addSeparator()
        self.act_refresh = tb.addAction(QtGui.QIcon("icons/refrescar.png"), "Refrescar")

        # Agregar botón de configuración
        self.act_config = QtWidgets.QAction(self)
        self.act_config.setIcon(QtGui.QIcon("icons/configuracion.png"))
        self.act_config.setText("Configuración")
        self.act_config.setStatusTip("Abrir configuración")
        self.act_config.triggered.connect(self.show_config)
        tb.addAction(self.act_config)

    # Atajos de teclado
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+T"), self, activated=self.new_tab_here)
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+W"), self, activated=lambda: self.close_tab(self.tabs.currentIndex()))
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+C"), self, activated=self.on_copy)
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+X"), self, activated=self.on_cut)
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+V"), self, activated=self.on_paste)
        QtWidgets.QShortcut(QtGui.QKeySequence("F5"), self, activated=self.on_refresh)
        QtWidgets.QShortcut(QtGui.QKeySequence("Alt+Izquierda"), self, activated=self.on_back)
        QtWidgets.QShortcut(QtGui.QKeySequence("Alt+Derecha"), self, activated=self.on_forward)
        QtWidgets.QShortcut(QtGui.QKeySequence("Alt+Arriba"), self, activated=self.on_up)
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+Q"), self, activated=self.close)

        self.status = self.statusBar()

        # Conexiones
        self.act_newtab.triggered.connect(self.new_tab_here)
        self.act_newfolder.triggered.connect(self.on_new_folder)
        self.act_rename.triggered.connect(self.on_rename)
        self.act_delete.triggered.connect(self.on_delete)
        self.act_copy.triggered.connect(self.on_copy)
        self.act_cut.triggered.connect(self.on_cut)
        self.act_paste.triggered.connect(self.on_paste)
        self.act_refresh.triggered.connect(self.on_refresh)
        self.act_up.triggered.connect(self.on_up)
        self.act_back.triggered.connect(self.on_back)
        self.act_forward.triggered.connect(self.on_forward)

        # Pestaña inicial
        self.new_tab(Path.home())

        # Actualizar estado de botones
        self.tabs.currentChanged.connect(self.update_navigation_buttons)
        
    def update_navigation_buttons(self):
        tab = self.current_tab()
        if tab:
            self.act_back.setEnabled(tab.history_index > 0)
            self.act_forward.setEnabled(tab.history_index < len(tab.history) - 1)
        else:
            self.act_back.setEnabled(False)
            self.act_forward.setEnabled(False)

    # ------- Tabs -------
    def current_tab(self) -> FileTab:
        w = self.tabs.currentWidget()
        return w if isinstance(w, FileTab) else None

    def new_tab(self, path: Path):
        tab = FileTab(path)
        tab.statusMessage.connect(self.status.showMessage)
        tab.requestOpenPath.connect(self.new_tab)
        idx = self.tabs.addTab(tab, path.name or str(path))
        self.tabs.setCurrentIndex(idx)
        self.address.setText(str(tab.current_path))

    def new_tab_here(self):
        tab = self.current_tab()
        if tab:
            self.new_tab(tab.current_path)

    def close_tab(self, index):
        if self.tabs.count() > 1:
            self.tabs.removeTab(index)

    # ------- Toolbar actions -------
    def go_address(self):
        path = Path(self.address.text()).expanduser()
        tab = self.current_tab()
        if tab and path.exists():
            tab.set_directory(path)
            self.tabs.setTabText(self.tabs.currentIndex(), path.name or str(path))
            self.update_navigation_buttons()  # Añadir esta línea
        else:
            self.status.showMessage("Ruta inválida")

    def on_new_folder(self):
        tab = self.current_tab()
        if tab:
            tab.new_folder()

    def on_rename(self):
        tab = self.current_tab()
        if tab:
            tab.rename_selected()

    def on_delete(self):
        tab = self.current_tab()
        if tab:
            tab.delete_selected()

    def on_copy(self):
        tab = self.current_tab()
        if tab:
            tab.copy_selected()

    def on_cut(self):
        tab = self.current_tab()
        if tab:
            tab.cut_selected()

    def on_paste(self):
        tab = self.current_tab()
        if tab:
            tab.paste_into_current()

    def on_refresh(self):
        tab = self.current_tab()
        if tab:
            tab.refresh()

    def on_up(self):
        tab = self.current_tab()
        if tab:
            tab.go_up()
            self.address.setText(str(tab.current_path))
            self.tabs.setTabText(self.tabs.currentIndex(), tab.current_path.name or str(tab.current_path))
            self.update_navigation_buttons()  # Añadir esta línea

    def on_back(self):
        tab = self.current_tab()
        if tab and tab.go_back():
            self.address.setText(str(tab.current_path))
            self.tabs.setTabText(self.tabs.currentIndex(), tab.current_path.name or str(tab.current_path))
            self.update_navigation_buttons()

    def on_forward(self):
        tab = self.current_tab()
        if tab and tab.go_forward():
            self.address.setText(str(tab.current_path))
            self.tabs.setTabText(self.tabs.currentIndex(), tab.current_path.name or str(tab.current_path))
            self.update_navigation_buttons()

    def show_config(self):
        dialog = ConfigDialog(self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            # Actualizar la configuración en todas las pestañas
            for i in range(self.tabs.count()):
                tab = self.tabs.widget(i)
                if isinstance(tab, FileTab):
                    tab.apply_settings()

    def closeEvent(self, event):
        """Guardar geometría de la ventana al cerrar"""
        settings = QtCore.QSettings()
        settings.setValue("window_geometry", self.saveGeometry())
        super().closeEvent(event)

# ---------------------- Diálogo de configuración ----------------------
class ConfigDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuración")
        self.setMinimumWidth(400)
        
        layout = QtWidgets.QVBoxLayout(self)
        
        # Crear un QTabWidget para las pestañas
        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs)
        
        # Pestaña General (contenido existente)
        general_tab = QtWidgets.QWidget()
        general_layout = QtWidgets.QVBoxLayout(general_tab)
        
        # Grupo de opciones de visualización
        group_view = QtWidgets.QGroupBox("Visualización")
        group_layout = QtWidgets.QVBoxLayout()
        
        self.show_hidden = QtWidgets.QCheckBox("Mostrar archivos ocultos")
        self.show_hidden.setChecked(QtCore.QSettings().value("show_hidden", False, bool))
        group_layout.addWidget(self.show_hidden)
        
        self.show_thumbnails = QtWidgets.QCheckBox("Mostrar miniaturas de imágenes")
        self.show_thumbnails.setChecked(QtCore.QSettings().value("show_thumbnails", True, bool))
        group_layout.addWidget(self.show_thumbnails)
        
        view_layout = QtWidgets.QHBoxLayout()
        view_layout.addWidget(QtWidgets.QLabel("Tipo de vista:"))
        self.view_mode = QtWidgets.QComboBox()
        self.view_mode.addItems(["Lista", "Iconos", "Iconos grandes"])
        current_view = QtCore.QSettings().value("view_mode", "Lista")
        self.view_mode.setCurrentText(current_view)
        view_layout.addWidget(self.view_mode)
        group_layout.addLayout(view_layout)
        
        group_view.setLayout(group_layout)
        general_layout.addWidget(group_view)
        
        # Grupo de permisos
        group_perms = QtWidgets.QGroupBox("Permisos")
        perms_layout = QtWidgets.QVBoxLayout()
        
        self.auto_exec = QtWidgets.QCheckBox("Dar permisos de ejecución automáticamente a scripts")
        self.auto_exec.setToolTip("Aplicar chmod +x automáticamente a archivos .sh, .py, etc.")
        self.auto_exec.setChecked(QtCore.QSettings().value("auto_exec", False, bool))
        perms_layout.addWidget(self.auto_exec)
        
        exec_layout = QtWidgets.QHBoxLayout()
        exec_layout.addWidget(QtWidgets.QLabel("Extensiones ejecutables:"))
        self.exec_extensions = QtWidgets.QLineEdit()
        self.exec_extensions.setPlaceholderText("sh py bash pl rb")
        self.exec_extensions.setText(QtCore.QSettings().value("exec_extensions", "sh py bash pl rb"))
        exec_layout.addWidget(self.exec_extensions)
        perms_layout.addLayout(exec_layout)
        
        group_perms.setLayout(perms_layout)
        general_layout.addWidget(group_perms)
        
        # Añadir pestaña General
        self.tabs.addTab(general_tab, "General")
        
        # Pestaña Temas
        themes_tab = QtWidgets.QWidget()
        themes_layout = QtWidgets.QVBoxLayout(themes_tab)
        
        # Grupo de temas
        group_themes = QtWidgets.QGroupBox("Temas de la aplicación")
        themes_group_layout = QtWidgets.QVBoxLayout()
        
        # Selector de tema
        theme_layout = QtWidgets.QHBoxLayout()
        theme_layout.addWidget(QtWidgets.QLabel("Tema:"))
        self.theme_selector = QtWidgets.QComboBox()
        self.theme_selector.addItems(["Claro", "Oscuro", "Sistema"])
        current_theme = QtCore.QSettings().value("theme", "Sistema")
        self.theme_selector.setCurrentText(current_theme)
        theme_layout.addWidget(self.theme_selector)
        themes_group_layout.addLayout(theme_layout)
        
        # Color personalizado
        color_layout = QtWidgets.QHBoxLayout()
        color_layout.addWidget(QtWidgets.QLabel("Color de acento:"))
        self.accent_color = QtWidgets.QPushButton()
        self.accent_color.setFixedWidth(100)
        current_color = QtCore.QSettings().value("accent_color", "#0078D7")
        self.accent_color.setStyleSheet(f"background-color: {current_color};")
        self.accent_color.clicked.connect(self.choose_color)
        color_layout.addWidget(self.accent_color)
        themes_group_layout.addLayout(color_layout)
        
        group_themes.setLayout(themes_group_layout)
        themes_layout.addWidget(group_themes)
        themes_layout.addStretch()
        
        # Añadir pestaña Temas
        self.tabs.addTab(themes_tab, "Temas")
        
        # Pestaña Acerca de...
        about_tab = QtWidgets.QWidget()
        about_layout = QtWidgets.QVBoxLayout(about_tab)
        
        # Logo o ícono
        logo_label = QtWidgets.QLabel()
        logo_label.setPixmap(QtGui.QIcon("icons/about.png").pixmap(64, 64))
        logo_label.setAlignment(Qt.AlignCenter)
        about_layout.addWidget(logo_label)
        
        # Información de la aplicación
        info_text = QtWidgets.QLabel(
            f"<h2>{APP_NAME}</h2>"
            "<p>Gestor de archivos para Linux en Python (PyQt5)</p>"
            "<p>Versión 1.0</p>"
            "<p>© 2023 - Tu Nombre</p>"
            "<p><a href='https://github.com/tuusuario/pyexplorer'>GitHub</a></p>"
        )
        info_text.setOpenExternalLinks(True)
        info_text.setAlignment(Qt.AlignCenter)
        about_layout.addWidget(info_text)
        
        about_layout.addStretch()
        
        # Añadir pestaña Acerca de...
        self.tabs.addTab(about_tab, "Acerca de...")
        
        # Botones
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_color(self):
        color = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(QtCore.QSettings().value("accent_color", "#0078D7")),
            self,
            "Elegir color de acento"
        )
        if color.isValid():
            self.accent_color.setStyleSheet(f"background-color: {color.name()};")
    
    def accept(self):
        # Guardar configuración
        settings = QtCore.QSettings()
        settings.setValue("show_hidden", self.show_hidden.isChecked())
        settings.setValue("show_thumbnails", self.show_thumbnails.isChecked())
        settings.setValue("view_mode", self.view_mode.currentText())
        settings.setValue("auto_exec", self.auto_exec.isChecked())
        settings.setValue("exec_extensions", self.exec_extensions.text())
        settings.setValue("theme", self.theme_selector.currentText())
        settings.setValue("accent_color", self.accent_color.palette().color(QtGui.QPalette.Button).name())
        
        # Aplicar tema
        app = QtWidgets.QApplication.instance()
        theme = self.theme_selector.currentText()
        
        # Cargar estilos CSS
        css_file = Path(__file__).parent / "themes.css"
        if css_file.exists():
            with open(css_file, "r") as f:
                css = f.read()
                
        if theme == "Oscuro":
            app.setProperty("theme", "dark")
            app.setStyleSheet(css)  # Recargar estilos
            app.setPalette(self.get_dark_palette())
        elif theme == "Claro":
            app.setProperty("theme", "light")
            app.setStyleSheet(css)  # Recargar estilos
            app.setPalette(self.get_light_palette())
        else:  # Sistema
            if QtWidgets.QApplication.palette().window().color().lightness() < 128:
                app.setProperty("theme", "dark")
                app.setStyleSheet(css)  # Recargar estilos
                app.setPalette(self.get_dark_palette())
            else:
                app.setProperty("theme", "light")
                app.setStyleSheet(css)  # Recargar estilos
                app.setPalette(self.get_light_palette())

        # Forzar actualización visual
        app.processEvents()
        
        super().accept()

    def get_dark_palette(self):
        palette = QtGui.QPalette()
        palette.setColor(QtGui.QPalette.Window, QtGui.QColor(53, 53, 53))
        palette.setColor(QtGui.QPalette.WindowText, QtGui.QColor(255, 255, 255))
        palette.setColor(QtGui.QPalette.Base, QtGui.QColor(42, 42, 42))
        palette.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(66, 66, 66))
        palette.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor(255, 255, 255))
        palette.setColor(QtGui.QPalette.ToolTipText, QtGui.QColor(255, 255, 255))
        palette.setColor(QtGui.QPalette.Text, QtGui.QColor(255, 255, 255))
        palette.setColor(QtGui.QPalette.Button, QtGui.QColor(53, 53, 53))
        palette.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(255, 255, 255))
        palette.setColor(QtGui.QPalette.Link, QtGui.QColor(42, 130, 218))
        palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor(42, 130, 218))
        palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor(255, 255, 255))
        return palette

    def get_light_palette(self):
        palette = QtGui.QPalette()
        palette.setColor(QtGui.QPalette.Window, QtGui.QColor(240, 240, 240))
        palette.setColor(QtGui.QPalette.WindowText, QtGui.QColor(0, 0, 0))
        palette.setColor(QtGui.QPalette.Base, QtGui.QColor(255, 255, 255))
        palette.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(245, 245, 245))
        palette.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor(0, 0, 0))
        palette.setColor(QtGui.QPalette.ToolTipText, QtGui.QColor(0, 0, 0))
        palette.setColor(QtGui.QPalette.Text, QtGui.QColor(0, 0, 0))
        palette.setColor(QtGui.QPalette.Button, QtGui.QColor(240, 240, 240))
        palette.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(0, 0, 0))
        palette.setColor(QtGui.QPalette.Link, QtGui.QColor(0, 120, 215))
        palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor(0, 120, 215))
        palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor(255, 255, 255))
        return palette

# ---------------------- Main ----------------------

def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(QtGui.QIcon(str(Path(__file__).parent / "icons/icon.png")))  # <-- Icono de la app
    
    # Cargar estilos CSS
    css_file = Path(__file__).parent / "themes.css"
    if css_file.exists():
        with open(css_file, "r") as f:
            app.setStyleSheet(f.read())
    
    # Crear una instancia temporal de ConfigDialog para usar sus métodos de paleta
    config = ConfigDialog()
    
    # Aplicar tema según configuración
    settings = QtCore.QSettings()
    theme = settings.value("theme", "Sistema")
    if theme == "Oscuro":
        app.setProperty("theme", "dark")
        app.setPalette(config.get_dark_palette())
    elif theme == "Claro":
        app.setProperty("theme", "light")
        app.setPalette(config.get_light_palette())
    else:  # Sistema
        if QtWidgets.QApplication.palette().window().color().lightness() < 128:
            app.setProperty("theme", "dark")
            app.setPalette(config.get_dark_palette())
        else:
            app.setProperty("theme", "light")
            app.setPalette(config.get_light_palette())
    
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
