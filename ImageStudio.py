import os
import sys
import io

APP_VERSION = "1.7"


class _NullStream(io.TextIOBase):
    """--noconsole ile paketlenen exe'de sys.stdout/sys.stderr None olur.
    rembg'in model indirme ilerleme cubugu (pooch/tqdm) stderr'e yazmaya calisip
    "'NoneType' object has no attribute 'write'" hatasi veriyordu. Bu yutucu akis
    o yaziyi sessizce yutar. Ucuncu parti importlardan ONCE kurulmalidir."""

    def write(self, s):
        return len(s)

    def flush(self):
        pass

    def isatty(self):
        return False


if sys.stdout is None:
    sys.stdout = _NullStream()
if sys.stderr is None:
    sys.stderr = _NullStream()

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import subprocess
import threading
import winsound
import time
import tempfile
import shutil
import json
import queue

try:
    from PIL import Image, ImageOps
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

try:
    from rembg import remove, new_session
    HAS_REMBG = True
    REMBG_ERROR = ""
except Exception as e:
    HAS_REMBG = False
    REMBG_ERROR = str(e)

# Sekme adları (tek kaynak) ve ayar dosyasında kullanılan kısa anahtarları
TAB_CONVERT = "🔄 Format Conversion"
TAB_OPTIMIZE = "🗜️ Web Optimize"
TAB_RESIZE = "📐 Image Resizing"
TAB_FAVICON = "🎯 Favicon Generator"
TAB_AI = "🪄 AI Background Removal"
TAB_KEYS = {TAB_CONVERT: "convert", TAB_OPTIMIZE: "optimize", TAB_RESIZE: "resize",
            TAB_FAVICON: "favicon", TAB_AI: "ai"}
FAVICON_SIZES = (16, 32, 48, 64, 128, 256)

# Sürüklenen / seçilen dosyalardan kabul edilen görsel uzantıları
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".jfif", ".webp", ".avif", ".heic", ".heif",
              ".jxl", ".bmp", ".gif", ".tif", ".tiff", ".ico", ".cur", ".svg",
              ".psd", ".tga"}

# Son kullanılan ayarlar burada saklanır; uygulama açılışta geri yükler.
SETTINGS_PATH = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                             "UltimateImageStudio", "settings.json")

def resource_path(rel):
    """Geliştirme ortamında da, PyInstaller ile paketlenmiş exe içinde de
    (sys._MEIPASS) bir kaynak dosyanın doğru yolunu döndürür."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)

# --- MODERN UI SETTINGS ---
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

class UltimateImageStudio(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"Ultimate Image Studio Pro v{APP_VERSION} - Smart UI & Filters")
        self.geometry("850x950")
        self.resizable(True, True)
        # --- ÖZEL İKON ENTEGRASYONU ---
        # CustomTkinter, kendi init'i sırasında pencere ikonunu sıfırlar; bu yüzden
        # ikonu hem hemen hem de kısa bir gecikmeyle (init bittikten sonra) uyguluyoruz.
        # Aksi halde başlıkta ve görev çubuğunda varsayılan Tk ikonu görünür.
        self._icon_path = resource_path("icon.ico")
        self._icon_png = resource_path("icon.png")
        self._icon_photo = None
        self._apply_icon()
        self.after(200, self._apply_icon)
        self.after(600, self._apply_icon)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.is_processing = False
        
        # Original Image Dimensions for Aspect Ratio
        self.orig_w = None
        self.orig_h = None
        self._updating_ratio = False

        # Çoklu girdi: gerçek liste burada; giriş kutusu yalnızca özet gösterir.
        self.input_files = []
        self._inputs_display = ""
        # İş parçacığından gelen tüm arayüz işleri bu kuyruk üzerinden ana
        # iş parçacığında çalıştırılır (Tk iş parçacığı güvenli değildir).
        self._ui_queue = queue.Queue()
        self._drain_after_id = None
        self._rembg_session = None
        self._dnd_callbacks = []

        self.input_file = ctk.StringVar()
        self.output_dir = ctk.StringVar()
        
        self.supported_formats = ["png", "webp", "jpg", "jpeg", "ico", "avif", "jxl", "svg", "heic", "heif", "bmp", "bmp3", "cur"]
        self.lossless_formats = ["png", "bmp", "bmp3", "ico", "cur", "svg"]
        
        self.sweet_spots = {
            "jpg":  {"web": 80, "hq": 90},
            "jpeg": {"web": 80, "hq": 90},
            "webp": {"web": 75, "hq": 85},
            "avif": {"web": 60, "hq": 75},
            "heic": {"web": 60, "hq": 75},
            "heif": {"web": 60, "hq": 75},
            "jxl":  {"web": 80, "hq": 90}
        }

        # --- PRO ENCODER TESPİTİ (ImageOptim tarzı web optimizasyonu) ---
        # cjpeg=MozJPEG, cwebp=Google WebP, pngquant/oxipng=PNG, hepsi PATH'te aranır.
        self.encoders = {
            tool: bool(shutil.which(tool))
            for tool in ("cjpeg", "cwebp", "pngquant", "oxipng")
        }

        # --- "WEB OPTIMIZE" SEKMESİ KALİTE SEVİYELERİ (formatı değiştirmeden) ---
        # ImageOptim'in Low/Medium/High mantığı. PNG bir kalite aralığı (pngquant) alır,
        # diğerleri tek bir kalite değeri. "png_floor", aralığın alt sınırıdır.
        self.optimize_levels = {
            "Low":    {"jpg": 58, "webp": 55, "avif": 42, "heic": 42, "jxl": 60, "png": "35-65"},
            "Medium": {"jpg": 70, "webp": 68, "avif": 52, "heic": 52, "jxl": 75, "png": "50-80"},
            "High":   {"jpg": 84, "webp": 82, "avif": 65, "heic": 65, "jxl": 88, "png": "70-92"},
        }

        # 1. FILE SELECTION AREA
        frame_files = self.create_card(self, "🖼️ Media Selection")
        frame_files.grid(row=0, column=0, sticky="nsew", padx=15, pady=10)
        
        inner_files = ctk.CTkFrame(frame_files, fg_color="transparent")
        inner_files.pack(fill="x", expand=True, padx=5, pady=5)
        inner_files.columnconfigure(1, weight=1)
        
        ctk.CTkLabel(inner_files, text="Input Image(s):").grid(row=0, column=0, padx=15, pady=10, sticky="w")
        self.entry_img = ctk.CTkEntry(inner_files, textvariable=self.input_file, placeholder_text="Select or drop one or more images...")
        self.entry_img.grid(row=0, column=1, sticky="ew", padx=10, pady=10)
        ctk.CTkButton(inner_files, text="Browse", width=100, command=self.select_image).grid(row=0, column=2, padx=10)

        ctk.CTkLabel(inner_files, text="Output Folder:").grid(row=1, column=0, padx=15, pady=(0, 6), sticky="w")
        self.entry_out = ctk.CTkEntry(inner_files, textvariable=self.output_dir, placeholder_text="Select destination folder...")
        self.entry_out.grid(row=1, column=1, sticky="ew", padx=10, pady=(0, 6))
        ctk.CTkButton(inner_files, text="Browse", width=100, command=self.select_output_dir).grid(row=1, column=2, padx=10, pady=(0, 6))

        self.lbl_drop_hint = ctk.CTkLabel(
            inner_files, text="", text_color="#8a8a8a", font=("Arial", 11))
        self.lbl_drop_hint.grid(row=2, column=0, columnspan=3, padx=15, pady=(0, 10), sticky="w")

        # 2. STUDIO TABS
        self.tabview = ctk.CTkTabview(self, command=self.on_tab_change)
        self.tabview.grid(row=1, column=0, sticky="nsew", padx=15, pady=5)

        self.tabs = {}

        self.create_convert_tab(TAB_CONVERT)
        self.create_optimize_tab(TAB_OPTIMIZE)
        self.create_resize_tab(TAB_RESIZE)
        self.create_favicon_tab(TAB_FAVICON)
        self.create_ai_tab(TAB_AI)

        # 3. ACTION BUTTON
        self.btn_start = ctk.CTkButton(
            self, text="🚀 PROCESS SELECTED TAB", 
            font=("Arial", 16, "bold"), height=50, corner_radius=25,
            command=self.start_thread
        )
        self.btn_start.grid(row=2, column=0, sticky="ew", padx=15, pady=15)

        # 4. STUDIO TERMINAL
        frame_log = self.create_card(self, "📟 Studio Terminal")
        frame_log.grid(row=3, column=0, sticky="nsew", padx=15, pady=(0, 15))
        
        self.txt_log = ctk.CTkTextbox(frame_log, font=("Consolas", 11), text_color="#00FF00", fg_color="#000000", corner_radius=10)
        self.txt_log.pack(fill="both", expand=True, padx=10, pady=10)
        self.txt_log.configure(state="disabled")

        self._load_settings()
        self.on_tab_change()
        self._log_encoder_status()

        self._drain_ui_queue()
        if self._enable_drag_and_drop():
            self.lbl_drop_hint.configure(
                text="💡 Drag & drop one or more images (or a folder) anywhere on this window — works on every tab.")
        else:
            self.lbl_drop_hint.configure(text="💡 Tip: Browse lets you select several images at once.")

    def _log_encoder_status(self):
        active = [t for t, ok in self.encoders.items() if ok]
        missing = [t for t, ok in self.encoders.items() if not ok]
        if active:
            self.log(f"🌍 Web Optimize aktif kodlayıcılar: {', '.join(active)}")
        if missing:
            self.log(f"ℹ️ Eksik kodlayıcılar (ImageMagick'e düşülür): {', '.join(missing)}")
            self.log("   Kurmak için:  scoop install mozjpeg libwebp pngquant oxipng")

    def _apply_icon(self):
        """Pencere + görev çubuğu ikonunu uygular. CTk init'i ezdiği için birkaç kez çağrılır.
        Tk (iconbitmap/iconphoto) başlık çubuğunu, Win32 WM_SETICON ise görev çubuğunu hedefler."""
        # Başlık çubuğu (Tk yöntemleri)
        try:
            if os.path.exists(self._icon_path):
                self.iconbitmap(self._icon_path)
        except Exception:
            pass
        try:
            if self._icon_photo is None and os.path.exists(self._icon_png):
                self._icon_photo = tk.PhotoImage(file=self._icon_png)
            if self._icon_photo is not None:
                self.iconphoto(True, self._icon_photo)
        except Exception:
            pass
        # Görev çubuğu: ikonu doğrudan pencereye WM_SETICON ile gönder (en güvenilir yol)
        try:
            if os.name == "nt" and os.path.exists(self._icon_path):
                import ctypes
                u = ctypes.windll.user32
                hwnd = u.GetParent(self.winfo_id()) or self.winfo_id()
                WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
                IMAGE_ICON, LR_LOADFROMFILE = 1, 0x00000010
                big = u.LoadImageW(None, self._icon_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
                small = u.LoadImageW(None, self._icon_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
                if big:
                    u.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big)
                if small:
                    u.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small)
        except Exception:
            pass

    def create_card(self, parent, title, **kwargs):
        card = ctk.CTkFrame(parent, corner_radius=15, border_width=1, border_color="#3A3A3A", fg_color="#242424", **kwargs)
        lbl = ctk.CTkLabel(card, text=title, font=("Segoe UI", 15, "bold"), text_color="#DDDDDD")
        lbl.pack(anchor="w", padx=15, pady=(10, 5))
        return card

    def on_tab_change(self):
        tab = self.tabview.get()
        if "AI" in tab: color, hover, text_color = "#8e44ad", "#732d91", "white"
        elif "Resizing" in tab: color, hover, text_color = "#e67e22", "#d35400", "white"
        elif "Optimize" in tab: color, hover, text_color = "#27ae60", "#1e8449", "white"
        elif "Favicon" in tab: color, hover, text_color = "#16a085", "#0e6655", "white"
        else: color, hover, text_color = "#2980b9", "#1f618d", "white"
            
        self.btn_start.configure(fg_color=color, hover_color=hover, text_color=text_color)
        self.tabview.configure(segmented_button_selected_color=color, segmented_button_selected_hover_color=hover)

    # --- TAB CREATORS ---
    def create_convert_tab(self, tab_name):
        self.tabview.add(tab_name)
        frame = self.tabview.tab(tab_name)
        
        vars = {
            "target_format": ctk.StringVar(value="webp"),
            "quality": ctk.IntVar(value=75),
            "web_optimize": ctk.BooleanVar(value=True)
        }
        self.tabs[tab_name] = vars

        def on_slider_change(val):
            choice = vars["target_format"].get()
            if choice not in self.lossless_formats:
                current_val = int(float(val))
                spots = self.sweet_spots.get(choice, {"web": 80, "hq": 90})
                
                if current_val == spots["web"]:
                    lbl_quality.configure(text=f"Quality: {current_val}  [🌍 Recommended for Web]", text_color="#2fa572")
                elif current_val == spots["hq"]:
                    lbl_quality.configure(text=f"Quality: {current_val}  [💎 Macro / High Detail]", text_color="#3498db")
                else:
                    lbl_quality.configure(text=f"Quality / Compression (0-100): {current_val}", text_color="#DDDDDD")

        def set_preset(preset_type):
            choice = vars["target_format"].get()
            if choice not in self.lossless_formats:
                spots = self.sweet_spots.get(choice, {"web": 80, "hq": 90})
                target_val = spots[preset_type]
                vars["quality"].set(target_val)
                on_slider_change(target_val)

        def update_quality_ui(choice):
            if choice in self.lossless_formats:
                lbl_quality.configure(text="Quality: [LOCKED] Lossless format selected", text_color="gray")
                slider_quality.configure(state="disabled", progress_color="gray", button_color="gray")
                btn_web.configure(state="disabled", text="🌍 Web Optimized")
                btn_hq.configure(state="disabled", text="💎 High Detail")
            else:
                spots = self.sweet_spots.get(choice, {"web": 80, "hq": 90})
                btn_web.configure(state="normal", text=f"🌍 Web ({spots['web']})")
                btn_hq.configure(state="normal", text=f"💎 High Detail ({spots['hq']})")
                slider_quality.configure(state="normal", progress_color="#1f538d", button_color="#1f538d")
                vars["quality"].set(spots["web"])
                on_slider_change(spots["web"])
                
        # Dışarıdan UI tetiklemek için fonksiyonları hafızaya al
        self.tabs[tab_name]["update_ui_func"] = update_quality_ui
        self.tabs[tab_name]["slider_func"] = on_slider_change

        ctk.CTkLabel(frame, text="Target Format:").grid(row=0, column=0, padx=15, pady=10, sticky="w")
        cb_format = ctk.CTkComboBox(frame, variable=vars["target_format"], values=self.supported_formats, width=200, command=update_quality_ui)
        cb_format.grid(row=0, column=1, padx=15, pady=10, sticky="w")
        
        lbl_quality = ctk.CTkLabel(frame, text="Quality / Compression (0-100):", font=("Arial", 12, "bold"))
        lbl_quality.grid(row=1, column=0, columnspan=2, padx=15, pady=(15, 5), sticky="w")
        
        frame_presets = ctk.CTkFrame(frame, fg_color="transparent")
        frame_presets.grid(row=2, column=0, columnspan=2, padx=15, pady=(0, 5), sticky="w")
        
        btn_web = ctk.CTkButton(frame_presets, text="🌍 Web Optimized", width=140, fg_color="#2fa572", hover_color="#1e6b4a", command=lambda: set_preset("web"))
        btn_web.pack(side="left", padx=(0, 10))
        btn_hq = ctk.CTkButton(frame_presets, text="💎 High Detail", width=140, fg_color="#1f538d", hover_color="#14375e", command=lambda: set_preset("hq"))
        btn_hq.pack(side="left")

        slider_quality = ctk.CTkSlider(frame, from_=1, to=100, variable=vars["quality"], width=300, command=on_slider_change)
        slider_quality.grid(row=3, column=0, columnspan=2, padx=15, pady=10, sticky="w")

        ctk.CTkCheckBox(
            frame, text="🌍 Web Optimize (MozJPEG / cwebp / pngquant — ImageOptim tarzı)",
            variable=vars["web_optimize"]
        ).grid(row=4, column=0, columnspan=2, padx=15, pady=(5, 10), sticky="w")

        update_quality_ui(vars["target_format"].get())

    def create_resize_tab(self, tab_name):
        self.tabview.add(tab_name)
        frame = self.tabview.tab(tab_name)
        
        vars = {
            "width": ctk.StringVar(value="1920"),
            "height": ctk.StringVar(value="1080"),
            "keep_aspect": ctk.BooleanVar(value=True),
            "filter": ctk.StringVar(value="Lanczos (Sharp - Best for Downscale)"),
            "target_format": ctk.StringVar(value="jpg"),
            "quality": ctk.IntVar(value=80),
            "web_optimize": ctk.BooleanVar(value=True)
        }
        self.tabs[tab_name] = vars

        # --- DİNAMİK ORAN (ASPECT RATIO) MOTORU ---
        def on_width_change(*args):
            if self._updating_ratio or not vars["keep_aspect"].get() or not self.orig_w: return
            try:
                w = float(vars["width"].get())
                h = int(w * (self.orig_h / self.orig_w))
                self._updating_ratio = True
                vars["height"].set(str(h))
                self._updating_ratio = False
            except ValueError:
                pass

        def on_height_change(*args):
            if self._updating_ratio or not vars["keep_aspect"].get() or not self.orig_h: return
            try:
                h = float(vars["height"].get())
                w = int(h * (self.orig_w / self.orig_h))
                self._updating_ratio = True
                vars["width"].set(str(w))
                self._updating_ratio = False
            except ValueError:
                pass

        vars["width"].trace_add("write", on_width_change)
        vars["height"].trace_add("write", on_height_change)
        # ------------------------------------------

        frame_dims = ctk.CTkFrame(frame, fg_color="transparent")
        frame_dims.grid(row=0, column=0, columnspan=2, sticky="ew")
        
        ctk.CTkLabel(frame_dims, text="Width (px):").grid(row=0, column=0, padx=15, pady=5, sticky="w")
        ctk.CTkEntry(frame_dims, textvariable=vars["width"], width=100).grid(row=0, column=1, padx=5, pady=5, sticky="w")
        
        ctk.CTkLabel(frame_dims, text="Height (px):").grid(row=0, column=2, padx=15, pady=5, sticky="w")
        ctk.CTkEntry(frame_dims, textvariable=vars["height"], width=100).grid(row=0, column=3, padx=5, pady=5, sticky="w")

        ctk.CTkCheckBox(frame, text="🔒 Keep Aspect Ratio (Auto-calculates Height/Width)", variable=vars["keep_aspect"]).grid(row=1, column=0, columnspan=2, padx=15, pady=10, sticky="w")

        # GÜNCELLENMİŞ ALGORİTMA LİSTESİ
        ctk.CTkLabel(frame, text="Optimization Filter:").grid(row=2, column=0, padx=15, pady=10, sticky="w")
        filter_options = [
            "Auto", 
            "Lanczos (Sharp - Best for Downscale)", 
            "Mitchell (Smooth - Soft edges, good for Portraits/Upscale)", 
            "Point (Pixel Art - No blur, exact pixel copy)"
        ]
        ctk.CTkComboBox(frame, variable=vars["filter"], values=filter_options, width=320).grid(row=2, column=1, padx=15, pady=10, sticky="w")

        # --- REZISE İÇİN SWEET SPOT KALİTE MOTORU ---
        def on_slider_change_resize(val):
            choice = vars["target_format"].get()
            if choice not in self.lossless_formats:
                current_val = int(float(val))
                spots = self.sweet_spots.get(choice, {"web": 80, "hq": 90})
                if current_val == spots["web"]:
                    lbl_quality.configure(text=f"Quality: {current_val}  [🌍 Recommended for Web]", text_color="#2fa572")
                elif current_val == spots["hq"]:
                    lbl_quality.configure(text=f"Quality: {current_val}  [💎 Macro / High Detail]", text_color="#3498db")
                else:
                    lbl_quality.configure(text=f"Quality: {current_val}", text_color="#DDDDDD")

        def set_preset_resize(preset_type):
            choice = vars["target_format"].get()
            if choice not in self.lossless_formats:
                spots = self.sweet_spots.get(choice, {"web": 80, "hq": 90})
                target_val = spots[preset_type]
                vars["quality"].set(target_val)
                on_slider_change_resize(target_val)

        def update_quality_ui_resize(choice):
            if choice in self.lossless_formats:
                lbl_quality.configure(text="Quality: [LOCKED] Lossless format", text_color="gray")
                slider_quality.configure(state="disabled", progress_color="gray", button_color="gray")
                btn_web.configure(state="disabled", text="🌍 Web")
                btn_hq.configure(state="disabled", text="💎 High Detail")
            else:
                spots = self.sweet_spots.get(choice, {"web": 80, "hq": 90})
                btn_web.configure(state="normal", text=f"🌍 Web ({spots['web']})")
                btn_hq.configure(state="normal", text=f"💎 High Detail ({spots['hq']})")
                slider_quality.configure(state="normal", progress_color="#e67e22", button_color="#e67e22") 
                vars["quality"].set(spots["web"])
                on_slider_change_resize(spots["web"])
                
        # Dışarıdan UI tetiklemek için fonksiyonları hafızaya al
        self.tabs[tab_name]["update_ui_func"] = update_quality_ui_resize
        self.tabs[tab_name]["slider_func"] = on_slider_change_resize
        self.tabs[tab_name]["filter_options"] = filter_options

        ctk.CTkLabel(frame, text="Output Format:").grid(row=3, column=0, padx=15, pady=10, sticky="w")
        ctk.CTkComboBox(frame, variable=vars["target_format"], values=self.supported_formats, width=150, command=update_quality_ui_resize).grid(row=3, column=1, padx=15, pady=10, sticky="w")

        lbl_quality = ctk.CTkLabel(frame, text="Quality / Compression:", font=("Arial", 12, "bold"))
        lbl_quality.grid(row=4, column=0, columnspan=2, padx=15, pady=(10, 5), sticky="w")
        
        frame_presets = ctk.CTkFrame(frame, fg_color="transparent")
        frame_presets.grid(row=5, column=0, columnspan=2, padx=15, pady=(0, 5), sticky="w")
        
        btn_web = ctk.CTkButton(frame_presets, text="🌍 Web", width=120, fg_color="#2fa572", hover_color="#1e6b4a", command=lambda: set_preset_resize("web"))
        btn_web.pack(side="left", padx=(0, 10))
        btn_hq = ctk.CTkButton(frame_presets, text="💎 High Detail", width=120, fg_color="#1f538d", hover_color="#14375e", command=lambda: set_preset_resize("hq"))
        btn_hq.pack(side="left")

        slider_quality = ctk.CTkSlider(frame, from_=1, to=100, variable=vars["quality"], width=300, command=on_slider_change_resize)
        slider_quality.grid(row=6, column=0, columnspan=2, padx=15, pady=10, sticky="w")

        ctk.CTkCheckBox(
            frame, text="🌍 Web Optimize (MozJPEG / cwebp / pngquant — ImageOptim tarzı)",
            variable=vars["web_optimize"]
        ).grid(row=7, column=0, columnspan=2, padx=15, pady=(5, 10), sticky="w")

        update_quality_ui_resize(vars["target_format"].get())

    def create_optimize_tab(self, tab_name):
        self.tabview.add(tab_name)
        frame = self.tabview.tab(tab_name)

        vars = {"level": ctk.StringVar(value="Medium")}
        self.tabs[tab_name] = vars

        ctk.CTkLabel(frame, text="🗜️ Smart Web Optimization — keeps the original format",
                     font=("Arial", 15, "bold"), text_color="#27ae60").grid(
                     row=0, column=0, columnspan=2, padx=15, pady=(18, 4), sticky="w")
        ctk.CTkLabel(frame, justify="left", text_color="#AAAAAA",
                     text="No conversion: re-encodes the image in its OWN format, strips metadata,\n"
                          "and finds the smallest visually-lossless size for the web.\n"
                          "Powered by MozJPEG / cwebp / pngquant + oxipng.").grid(
                     row=1, column=0, columnspan=2, padx=15, pady=(0, 14), sticky="w")

        ctk.CTkLabel(frame, text="Compression Level:", font=("Arial", 12, "bold")).grid(
            row=2, column=0, padx=15, pady=(6, 4), sticky="w")

        lbl_desc = ctk.CTkLabel(frame, text="", text_color="#2ecc71", font=("Arial", 12))

        def on_level(val):
            notes = {
                "Low":    "🪶 Low — smallest file; slight softening only on close pixel-peeping.",
                "Medium": "✅ Medium (recommended) — looks identical, big size savings.",
                "High":   "💎 High — sharpest; larger file but still below the original.",
            }
            lbl_desc.configure(text=notes.get(val, ""))

        self.tabs[tab_name]["level_func"] = on_level

        seg = ctk.CTkSegmentedButton(
            frame, values=["Low", "Medium", "High"], variable=vars["level"],
            command=on_level, selected_color="#27ae60", selected_hover_color="#1e8449")
        seg.grid(row=3, column=0, columnspan=2, padx=15, pady=(0, 6), sticky="w")

        lbl_desc.grid(row=4, column=0, columnspan=2, padx=15, pady=(2, 6), sticky="w")
        on_level("Medium")

        ctk.CTkLabel(frame, text="Output:  <name>_optimized.<same extension>   ·   Never larger than the original.",
                     text_color="#777777", font=("Arial", 11)).grid(
                     row=5, column=0, columnspan=2, padx=15, pady=(12, 4), sticky="w")

    def create_favicon_tab(self, tab_name):
        self.tabview.add(tab_name)
        frame = self.tabview.tab(tab_name)

        sizes = [16, 32, 48, 64, 128, 256]
        defaults = {16, 32, 48}
        vars = {f"sz_{s}": ctk.BooleanVar(value=(s in defaults)) for s in sizes}
        self.tabs[tab_name] = vars

        ctk.CTkLabel(frame, text="🎯 Favicon Generator — one multi-resolution .ico",
                     font=("Arial", 15, "bold"), text_color="#16a085").grid(
                     row=0, column=0, columnspan=6, padx=15, pady=(18, 4), sticky="w")
        ctk.CTkLabel(frame, justify="left", text_color="#AAAAAA",
                     text="Source = the Input Image(s) selected above. Best results: a square,\n"
                          "high-resolution (≥256px) PNG with transparency. Non-square images are\n"
                          "auto-padded so they aren't distorted. Each .ico contains every\n"
                          "checked resolution.").grid(
                     row=1, column=0, columnspan=6, padx=15, pady=(0, 12), sticky="w")

        ctk.CTkLabel(frame, text="Resolutions to include:", font=("Arial", 12, "bold")).grid(
            row=2, column=0, columnspan=6, padx=15, pady=(6, 4), sticky="w")

        frame_sizes = ctk.CTkFrame(frame, fg_color="transparent")
        frame_sizes.grid(row=3, column=0, columnspan=6, padx=15, pady=(0, 6), sticky="w")
        hints = {16: "browser tab", 32: "taskbar / retina", 48: "Windows",
                 64: "HiDPI", 128: "large", 256: "max / HiDPI"}
        for i, s in enumerate(sizes):
            ctk.CTkCheckBox(frame_sizes, text=f"{s}×{s}   ({hints[s]})",
                            variable=vars[f"sz_{s}"], width=200).grid(
                            row=i // 2, column=i % 2, padx=10, pady=6, sticky="w")

        def set_sizes(active):
            for s in sizes:
                vars[f"sz_{s}"].set(s in active)

        frame_presets = ctk.CTkFrame(frame, fg_color="transparent")
        frame_presets.grid(row=4, column=0, columnspan=6, padx=15, pady=(8, 4), sticky="w")
        ctk.CTkButton(frame_presets, text="✅ Standard (16/32/48)", width=180,
                      fg_color="#16a085", hover_color="#0e6655",
                      command=lambda: set_sizes({16, 32, 48})).pack(side="left", padx=(0, 10))
        ctk.CTkButton(frame_presets, text="📦 All sizes", width=100,
                      fg_color="#1f6f8b", hover_color="#16515f",
                      command=lambda: set_sizes(set(sizes))).pack(side="left")

        ctk.CTkLabel(frame, text="Output:  favicon.ico   ·   several images → <name>_favicon.ico each",
                     text_color="#777777", font=("Arial", 11)).grid(
                     row=5, column=0, columnspan=6, padx=15, pady=(12, 4), sticky="w")

    def create_ai_tab(self, tab_name):
        self.tabview.add(tab_name)
        frame = self.tabview.tab(tab_name)

        ctk.CTkLabel(frame, text="🤖 Powered by U^2-Net Artificial Intelligence", font=("Arial", 14, "bold"), text_color="#8e44ad").pack(pady=20)
        ctk.CTkLabel(frame, text="This tool automatically detects the main subject and removes the background.\nThe output will be strictly saved as a transparent .png file.").pack(pady=10)

# --- FILE HANDLING ---
    def select_image(self):
        paths = filedialog.askopenfilenames(
            title="Select Image(s)",
            filetypes=[("Image Files", " ".join(f"*{e}" for e in sorted(IMAGE_EXTS))),
                       ("All Files", "*.*")]
        )
        if paths:
            if isinstance(paths, str):
                paths = self.tk.splitlist(paths)
            self._set_inputs(list(paths))

    def _expand_inputs(self, paths):
        """Klasörleri (üst seviye) içindeki görsellere açar, desteklenmeyen dosyaları
        ve tekrarları eler, sırayı korur. (dosyalar, atlananlar) döndürür."""
        files, seen, skipped = [], set(), []

        def add(p):
            full = os.path.abspath(p)
            key = os.path.normcase(full)
            if key not in seen:
                seen.add(key)
                files.append(full)

        for p in paths:
            p = str(p).strip().strip('"')
            if not p:
                continue
            if os.path.isdir(p):
                try:
                    entries = sorted(os.listdir(p), key=str.lower)
                except OSError:
                    skipped.append(p)
                    continue
                for entry in entries:
                    full = os.path.join(p, entry)
                    if os.path.isfile(full) and os.path.splitext(entry)[1].lower() in IMAGE_EXTS:
                        add(full)
            elif os.path.isfile(p) and os.path.splitext(p)[1].lower() in IMAGE_EXTS:
                add(p)
            else:
                skipped.append(os.path.basename(p) or p)
        return files, skipped

    def _set_inputs(self, paths):
        """Girdi listesini ayarlar. Sekme ayarlarına (format, kalite, ölçüler)
        DOKUNMAZ: kullanıcının en son bıraktığı ayarlar korunur."""
        files, skipped = self._expand_inputs(paths)
        for item in skipped:
            self.log(f"⚠️ Skipped (not a supported image): {item}")
        if not files:
            self.log("⚠️ No supported image found in the selection.")
            return False

        self.input_files = files
        first = files[0]
        if len(files) == 1:
            self._inputs_display = first
            self.log(f"Image Selected: {os.path.basename(first)}")
        else:
            names = ", ".join(os.path.basename(f) for f in files[:3])
            more = f" +{len(files) - 3} more" if len(files) > 3 else ""
            self._inputs_display = f"{len(files)} files: {names}{more}"
            self.log(f"🗂️ {len(files)} images selected: {names}{more}")
        self.input_file.set(self._inputs_display)

        # Output klasörü girdinin klasörünü izler (mevcut davranış)
        yeni_klasor = os.path.dirname(first)
        self.output_dir.set(yeni_klasor)
        self.log(f"📂 Output Folder Auto-Set: {yeni_klasor}")

        # Ölçüler yalnızca en-boy oranı motoru için okunur; alanlar ezilmez.
        size = self._image_size(first)
        self.orig_w, self.orig_h = size if size else (None, None)
        if size:
            self.log(f"📐 Original Dimensions detected: {size[0]}x{size[1]}")
        else:
            self.log("⚠️ Dimensions could not be read automatically.")
        return True

    def _current_inputs(self):
        """İşlenecek dosyaları döndürür. Kullanıcı giriş kutusuna elle başka bir
        yol yazdıysa (gösterilen özetten farklıysa) o yol esas alınır."""
        text = self.input_file.get().strip().strip('"')
        if self.input_files and text == self._inputs_display:
            return list(self.input_files)
        if not text:
            return []
        if os.path.isdir(text):
            return self._expand_inputs([text])[0]
        return [text]

    def _on_files_dropped(self, paths):
        if self.is_processing:
            self.log("⏳ Busy — wait for the current job to finish, then drop again.")
            return
        self.log("=" * 60)
        self.log(f"📥 Dropped {len(paths)} item(s)")
        self._set_inputs(paths)

    def _image_size(self, path):
        """EXIF yönü uygulanmış (genişlik, yükseklik); okunamazsa None."""
        if HAS_PILLOW:
            try:
                with Image.open(path) as img:
                    return ImageOps.exif_transpose(img).size
            except Exception:
                pass
        try:
            r = subprocess.run(
                ["magick", path + "[0]", "-auto-orient", "-format", "%w %h", "info:"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=60, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            w, h = r.stdout.split()[:2]
            return int(w), int(h)
        except Exception:
            return None

    def _enable_drag_and_drop(self):
        """Windows'un yerleşik dosya bırakma mesajını (WM_DROPFILES) pencereye bağlar.
        Ek bağımlılık gerektirmez ve Tk 9 ile de çalışır. Başarılıysa True döner.
        Bırakma, pencerenin herhangi bir yerine (hangi sekme açıksa) yapılabilir."""
        if os.name != "nt":
            return False
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32
            LRESULT = ctypes.c_ssize_t
            WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                         wintypes.WPARAM, wintypes.LPARAM)
            user32.CallWindowProcW.restype = LRESULT
            user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                               wintypes.WPARAM, wintypes.LPARAM]
            user32.DefWindowProcW.restype = LRESULT
            user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                              wintypes.WPARAM, wintypes.LPARAM]
            user32.SetWindowLongPtrW.restype = ctypes.c_void_p
            user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
            user32.GetParent.restype = wintypes.HWND
            user32.GetParent.argtypes = [wintypes.HWND]
            shell32.DragQueryFileW.restype = wintypes.UINT
            shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                               ctypes.c_wchar_p, wintypes.UINT]
            shell32.DragFinish.restype = None
            shell32.DragFinish.argtypes = [ctypes.c_void_p]
            shell32.DragAcceptFiles.restype = None
            shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
            WM_DROPFILES, GWLP_WNDPROC = 0x0233, -4

            def make_proc(prev):
                def proc(hwnd, msg, wparam, lparam):
                    if msg == WM_DROPFILES:
                        try:
                            count = shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)
                            files = []
                            for i in range(count):
                                n = shell32.DragQueryFileW(wparam, i, None, 0)
                                buf = ctypes.create_unicode_buffer(n + 1)
                                shell32.DragQueryFileW(wparam, i, buf, n + 1)
                                files.append(buf.value)
                            # Pencere yordamı içinde Tk'ya dokunulmaz; yalnızca kuyruğa atılır.
                            self._ui_queue.put(lambda f=files: self._on_files_dropped(f))
                        except Exception:
                            pass
                        finally:
                            try:
                                shell32.DragFinish(wparam)
                            except Exception:
                                pass
                        return 0
                    if prev[0]:
                        return user32.CallWindowProcW(prev[0], hwnd, msg, wparam, lparam)
                    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
                return proc

            self.update_idletasks()
            inner = self.winfo_id()
            outer = user32.GetParent(inner)
            for hwnd in dict.fromkeys(h for h in (inner, outer) if h):
                prev = [None]
                cb = WNDPROC(make_proc(prev))
                old = user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC,
                                               ctypes.cast(cb, ctypes.c_void_p).value)
                if not old:
                    continue
                prev[0] = old
                self._dnd_callbacks.append(cb)   # geri çağrı çöpe gitmesin
                shell32.DragAcceptFiles(hwnd, True)
            return bool(self._dnd_callbacks)
        except Exception:
            return False

    def select_output_dir(self):
        path = filedialog.askdirectory(title="Select Destination Folder")
        if path:
            self.output_dir.set(path)

    def log(self, message):
        """İş parçacığı güvenli: ana iş parçacığı dışından çağrılırsa satır kuyruğa
        atılır ve Tk'ya yalnızca ana iş parçacığından dokunulur."""
        if threading.current_thread() is threading.main_thread():
            self._log_now(message)
        else:
            self._ui_queue.put(lambda m=message: self._log_now(m))

    def _log_now(self, message):
        try:
            self.txt_log.configure(state='normal')
            self.txt_log.insert(tk.END, str(message) + "\n")
            self.txt_log.see(tk.END)
            self.txt_log.configure(state='disabled')
        except Exception:
            pass

    def _ui(self, fn):
        """fn'i ana (Tk) iş parçacığında çalıştırır."""
        if threading.current_thread() is threading.main_thread():
            fn()
        else:
            self._ui_queue.put(fn)

    def _drain_ui_queue(self):
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception:
                    pass
        except queue.Empty:
            pass
        try:
            self._drain_after_id = self.after(40, self._drain_ui_queue)
        except Exception:
            self._drain_after_id = None

    def destroy(self):
        try:
            if self._drain_after_id is not None:
                self.after_cancel(self._drain_after_id)
        except Exception:
            pass
        super().destroy()

    def on_closing(self):
        try:
            self._save_settings(self._collect_settings())
        except Exception:
            pass
        self.destroy()
        os._exit(0)

    # --- SETTINGS (son bırakılan ayarlar kalıcıdır) ---
    def _collect_settings(self):
        """Arayüzdeki ayarları sade bir sözlüğe toplar. Ana iş parçacığında çağrılır;
        işçi iş parçacığı Tk değişkenlerine hiç dokunmadan bu kopyayla çalışır."""
        c, r = self.tabs[TAB_CONVERT], self.tabs[TAB_RESIZE]

        def quality(var):
            try:
                return int(float(var.get()))
            except Exception:
                return 75

        return {
            "version": 1,
            "active_tab": TAB_KEYS.get(self.tabview.get(), "convert"),
            "output_dir": self.output_dir.get().strip(),
            "convert": {"target_format": c["target_format"].get(),
                        "quality": quality(c["quality"]),
                        "web_optimize": bool(c["web_optimize"].get())},
            "optimize": {"level": self.tabs[TAB_OPTIMIZE]["level"].get()},
            "resize": {"width": r["width"].get().strip(),
                       "height": r["height"].get().strip(),
                       "keep_aspect": bool(r["keep_aspect"].get()),
                       "filter": r["filter"].get(),
                       "target_format": r["target_format"].get(),
                       "quality": quality(r["quality"]),
                       "web_optimize": bool(r["web_optimize"].get())},
            "favicon": {"sizes": [s for s in FAVICON_SIZES
                                  if self.tabs[TAB_FAVICON][f"sz_{s}"].get()]},
        }

    def _save_settings(self, settings):
        try:
            os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
            tmp = SETTINGS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
            os.replace(tmp, SETTINGS_PATH)
        except Exception:
            pass

    def _load_settings(self):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        if isinstance(data, dict):
            self._apply_settings(data)

    def _apply_settings(self, s):
        """Kayıtlı ayarları arayüze uygular. Bozuk/eski değerler sessizce atlanır."""
        def section(key):
            v = s.get(key)
            return v if isinstance(v, dict) else {}

        def valid_quality(v):
            return isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 100

        # Format önce (kalite arayüzünü sıfırlar), kalite sonra uygulanır.
        for tab, key in ((TAB_CONVERT, "convert"), (TAB_RESIZE, "resize")):
            d, t = section(key), self.tabs[tab]
            try:
                fmt = d.get("target_format")
                if fmt in self.supported_formats:
                    t["target_format"].set(fmt)
                    t["update_ui_func"](fmt)
                q = d.get("quality")
                if valid_quality(q) and t["target_format"].get() not in self.lossless_formats:
                    t["quality"].set(q)
                    t["slider_func"](q)
                if isinstance(d.get("web_optimize"), bool):
                    t["web_optimize"].set(d["web_optimize"])
            except Exception:
                pass

        try:
            d, t = section("resize"), self.tabs[TAB_RESIZE]
            if isinstance(d.get("keep_aspect"), bool):
                t["keep_aspect"].set(d["keep_aspect"])
            if d.get("filter") in t["filter_options"]:
                t["filter"].set(d["filter"])
            w, h = str(d.get("width", "")), str(d.get("height", ""))
            if w.isdigit() and h.isdigit() and int(w) > 0 and int(h) > 0:
                try:
                    self._updating_ratio = True
                    t["width"].set(w)
                    t["height"].set(h)
                finally:
                    self._updating_ratio = False
        except Exception:
            pass

        try:
            level = section("optimize").get("level")
            if level in self.optimize_levels:
                self.tabs[TAB_OPTIMIZE]["level"].set(level)
                self.tabs[TAB_OPTIMIZE]["level_func"](level)
        except Exception:
            pass

        try:
            sizes = section("favicon").get("sizes")
            if isinstance(sizes, list) and all(isinstance(x, int) for x in sizes):
                for sz in FAVICON_SIZES:
                    self.tabs[TAB_FAVICON][f"sz_{sz}"].set(sz in sizes)
        except Exception:
            pass

        out = s.get("output_dir")
        if isinstance(out, str) and out and os.path.isdir(out):
            self.output_dir.set(out)

        for name, key in TAB_KEYS.items():
            if key == s.get("active_tab"):
                try:
                    self.tabview.set(name)
                except Exception:
                    pass
                break

    # --- CORE PROCESSING ENGINES ---
    def _pil_can_open(self, path):
        """AI motorunun (Pillow) bu dosyayı açıp açamayacağını söyler.
        Pillow yoksa karar veremeyiz; rembg kendi denesin diye True döneriz."""
        if not HAS_PILLOW:
            return True
        try:
            with Image.open(path) as im:
                im.verify()
            return True
        except Exception:
            return False

    def _prepare_u2net_cache(self):
        """U^2-Net model önbelleğini hazırlar. Model ilk kullanımda ~176 MB olarak
        indirilir; kullanıcı bunu bilmezse uygulama donmuş sanılıyor. Ayrıca yarım
        kalmış indirmelerden kalan 0 byte'lık geçici dosyaları temizler."""
        try:
            cache_dir = os.path.join(os.path.expanduser("~"), ".u2net")
            model = os.path.join(cache_dir, "u2net.onnx")
            if os.path.isdir(cache_dir):
                for entry in os.listdir(cache_dir):
                    path = os.path.join(cache_dir, entry)
                    try:
                        if entry.startswith("tmp") and os.path.isfile(path) \
                                and os.path.getsize(path) == 0:
                            os.remove(path)
                    except OSError:
                        pass
            if not os.path.exists(model):
                self.log("⬇️ İlk çalıştırma: AI modeli indiriliyor (~176 MB, internet gerekir).")
                self.log("   Bu birkaç dakika sürebilir; uygulama donmuş değil, lütfen bekle...")
        except Exception:
            pass

    def _resolve_output_dir(self, first_input):
        """Çıktı klasörünü işlem BAŞLAMADAN doğrular. Boşsa dosya sessizce çalışma
        dizinine yazılıyordu; klasör yoksa iş bittikten sonra ham hata veriyordu.
        Kullanılabilir yolu döndürür, aksi halde None."""
        out_dir = self.output_dir.get().strip()
        if not out_dir:
            out_dir = os.path.dirname(os.path.abspath(first_input))
            self.output_dir.set(out_dir)
            self.log(f"ℹ️ Çıktı klasörü boştu → girdi klasörü kullanılıyor: {out_dir}")
        if not os.path.isdir(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
                self.log(f"📂 Çıktı klasörü oluşturuldu: {out_dir}")
            except OSError as e:
                self.log(f"❌ Çıktı klasörü oluşturulamadı: {out_dir}\n   {e}")
                return None
        if not os.access(out_dir, os.W_OK):
            self.log(f"❌ Çıktı klasörüne yazma izni yok: {out_dir}")
            return None
        return out_dir

    def start_thread(self):
        if self.is_processing:
            return

        files = self._current_inputs()
        if not files:
            messagebox.showerror("Error", "Please select or drop at least one input image first!")
            return
        missing = [f for f in files if not os.path.isfile(f)]
        files = [f for f in files if os.path.isfile(f)]
        for f in missing:
            self.log(f"⚠️ Skipped (file no longer exists): {f}")
        if not files:
            messagebox.showerror("Error", "The selected input image(s) no longer exist!")
            return

        # Ayarlar ANA iş parçacığında bir kez okunur; işçi yalnızca bu kopyayı kullanır.
        settings = self._collect_settings()
        tab = settings["active_tab"]
        if tab == "favicon" and not settings["favicon"]["sizes"]:
            messagebox.showerror("Error", "Select at least one favicon resolution (e.g. 16, 32, 48).")
            return
        if tab == "resize":
            w, h = settings["resize"]["width"], settings["resize"]["height"]
            if not (w.isdigit() and h.isdigit() and int(w) > 0 and int(h) > 0):
                messagebox.showerror("Error", "Width and height must be positive whole numbers.")
                return
        if tab == "ai" and not HAS_REMBG:
            self.log(f"❌ ERROR: AI Engine failed to load!\n⚠️ Hidden Detail: {REMBG_ERROR}")
            messagebox.showerror("Error", "The AI engine could not be loaded.\nSee the Studio Terminal for details.")
            return

        out_dir = self._resolve_output_dir(files[0])
        if out_dir is None:
            messagebox.showerror("Error", "Output folder is not usable.\nSee the Studio Terminal for details.")
            return

        self._save_settings(settings)   # son kullanılan ayarlar bir sonraki açılışta da kalsın
        self.is_processing = True
        self.btn_start.configure(state="disabled", text="⏳ PROCESSING...")
        threading.Thread(target=self.process_batch, args=(files, settings, out_dir),
                         daemon=True).start()

    # --- BATCH ENGINE (tek veya çok dosya, bütün sekmeler) ---
    def process_batch(self, files, settings, out_dir):
        """Seçilen/bırakılan tüm dosyaları aktif sekmenin ayarlarıyla sırayla işler.
        İşçi iş parçacığında çalışır; Tk'ya yalnızca _ui/log kuyruğu üzerinden dokunur."""
        tab = settings["active_tab"]
        label = next((name for name, key in TAB_KEYS.items() if key == tab), tab)
        total = len(files)
        results, failures = [], []
        # Aynı iş içinde çıktılar birbirini (ya da henüz işlenmemiş bir girdiyi) ezmesin.
        used = {os.path.normcase(os.path.abspath(f)) for f in files}
        try:
            self.log("=" * 60)
            self.log(f"🎬 STUDIO ENGINE STARTED: {label}"
                     + (f"  —  {total} files" if total > 1 else ""))

            if tab == "ai" and self._get_rembg_session() is None:
                failures = list(files)
                return

            for idx, path in enumerate(files, 1):
                if total > 1:
                    self._ui(lambda i=idx: self._set_busy_text(f"⏳ PROCESSING {i}/{total}..."))
                    self.log(f"── [{idx}/{total}] {os.path.basename(path)}")
                try:
                    out = self._process_one(path, tab, settings, out_dir, total > 1, used)
                except Exception as e:
                    self.log(f"❌ CRITICAL ERROR ({os.path.basename(path)}): {e}")
                    out = None
                if out:
                    results.append(out)
                else:
                    failures.append(path)
        finally:
            # Bayrak hemen düşer; buton ve bildirimler ana iş parçacığında geri gelir.
            self.is_processing = False
            self._ui(lambda: self._finish_batch(results, failures))

    def _set_busy_text(self, text):
        try:
            if self.is_processing:
                self.btn_start.configure(text=text)
        except Exception:
            pass

    def _unique_path(self, path, used):
        """Aynı toplu işte daha önce üretilmiş (veya bir girdiyle çakışan) bir yol
        gelirse _2, _3... ekler."""
        base, ext = os.path.splitext(path)
        candidate, n = path, 2
        while os.path.normcase(os.path.abspath(candidate)) in used:
            candidate = f"{base}_{n}{ext}"
            n += 1
        used.add(os.path.normcase(os.path.abspath(candidate)))
        return candidate

    def _get_rembg_session(self):
        """U^2-Net oturumunu bir kez yükleyip tekrar kullanır; her dosyada 176 MB'lık
        modelin yeniden yüklenmesini önler (toplu işte büyük hız farkı)."""
        if self._rembg_session is not None:
            return self._rembg_session
        self._prepare_u2net_cache()
        self.log("🧠 AI model loading...")
        try:
            self._rembg_session = new_session("u2net")
        except Exception as e:
            self.log(f"❌ AI model yüklenemedi: {type(e).__name__}: {e}")
            self.log("ℹ️ Model inmediyse internet bağlantını kontrol edip tekrar dene.")
            return None
        return self._rembg_session

    def _process_one(self, input_path, tab, s, out_dir, multi, used):
        """Tek bir dosyayı işler. Başarıda çıktı yolunu, aksi halde None döndürür."""
        filename = os.path.basename(input_path)
        name, src_ext = os.path.splitext(filename)

        if tab == "ai":
            output_path = self._unique_path(os.path.join(out_dir, f"{name}_NoBG.png"), used)
            self.log("🤖 AI Engine analyzing the image (CPU Mode)...")

            # AI motoru (Pillow) HEIC/JXL gibi formatları okuyamaz; önce ImageMagick
            # ile geçici PNG'ye çeviriyoruz.
            ai_source, tmp_png = input_path, None
            if not self._pil_can_open(input_path):
                self.log("🔄 Bu formatı AI motoru okuyamıyor → geçici PNG'ye çevriliyor...")
                tmp_png = self._stage1_png(input_path, [])
                if not tmp_png:
                    self.log("❌ Girdi PNG'ye çevrilemedi; bu dosya atlandı.")
                    return None
                ai_source = tmp_png
            try:
                with open(ai_source, 'rb') as i:
                    input_data = i.read()
                output_data = remove(input_data, session=self._rembg_session)
            except Exception as e:
                self.log(f"❌ AI Engine failed: {type(e).__name__}: {e}")
                if type(e).__name__ == "UnidentifiedImageError":
                    self.log("ℹ️ Bu görsel formatı desteklenmiyor ya da dosya bozuk.")
                return None
            finally:
                self._cleanup(tmp_png)

            try:
                with open(output_path, 'wb') as o:
                    o.write(output_data)
            except OSError as e:
                self.log(f"❌ AI sonucu hesaplandı ama kaydedilemedi: {e}")
                self.log(f"   Hedef klasör: {out_dir}")
                return None
            self.log("✨ AI Background removal successful!")
            return output_path

        if tab == "convert":
            c = s["convert"]
            target_fmt = c["target_format"]
            quality = str(int(c["quality"]))
            web = c["web_optimize"]
            # Dosya adına bu görselin kendi yüksekliği yazılır (toplu işte her dosya
            # farklı olabilir); okunamazsa orijinali ezmemek için "_web".
            size = self._image_size(input_path)
            height_tag = f"_{size[1]}px" if size else "_web"
            output_path = self._unique_path(
                os.path.join(out_dir, f"{name}{height_tag}.{target_fmt}"), used)
            q_label = quality if target_fmt not in self.lossless_formats else 'Lossless'
            self.log(f"🪄 Converting to {target_fmt.upper()} with Quality: {q_label}"
                     f"{' | 🌍 Web Optimize' if web else ''}")
            if self.encode(input_path, output_path, target_fmt, quality, web):
                self.log_savings(input_path, output_path)
                return output_path
            return None

        if tab == "resize":
            r = s["resize"]
            w, h = r["width"], r["height"]
            target_fmt = r["target_format"]
            keep_aspect = r["keep_aspect"]
            quality = str(int(r["quality"]))
            web = r["web_optimize"]
            resize_param = f"{w}x{h}" if keep_aspect else f"{w}x{h}!"
            aspect_tag = "KeepAspect" if keep_aspect else "Forced"
            magick_filter = r["filter"].split(" ")[0]
            output_path = self._unique_path(
                os.path.join(out_dir, f"{name}_Resized_{w}x{h}_{aspect_tag}.{target_fmt}"), used)

            pre_args = []
            if magick_filter != "Auto":
                pre_args.extend(["-filter", magick_filter])
            pre_args.extend(["-resize", resize_param])

            q_label = quality if target_fmt not in self.lossless_formats else 'Lossless'
            self.log(f"📏 Resizing to {w}x{h} (Filter: {magick_filter})")
            self.log(f"🪄 Output Format: {target_fmt.upper()}, Quality: {q_label}"
                     f"{' | 🌍 Web Optimize' if web else ''}")
            if self.encode(input_path, output_path, target_fmt, quality, web, pre_args=pre_args):
                self.log_savings(input_path, output_path)
                return output_path
            return None

        if tab == "optimize":
            level = s["optimize"]["level"]
            preset = self.optimize_levels.get(level, self.optimize_levels["Medium"])
            # Formatı KORU: çıktının uzantısı girişle aynı (convert yok).
            ext = src_ext.lower().lstrip(".")
            if not ext:
                self.log(f"⚠️ {filename}: dosya uzantısı yok, format korunamaz — atlandı.")
                return None
            enc_fmt = "jpg" if ext in ("jpg", "jpeg", "jfif") else ext
            output_path = self._unique_path(
                os.path.join(out_dir, f"{name}_optimized.{ext}"), used)

            self.log(f"🗜️ Web Optimize ({level}) — format korunuyor: .{ext.upper()}")
            if enc_fmt == "png":
                ok = self.encode(input_path, output_path, "png", "0", True,
                                 png_quality=preset["png"])
            else:
                ok = self.encode(input_path, output_path, enc_fmt,
                                 str(preset.get(enc_fmt, 75)), True)
            if not ok:
                return None
            # ImageOptim ilkesi: çıktı asla orijinalden büyük olmasın.
            try:
                if os.path.getsize(output_path) >= os.path.getsize(input_path):
                    shutil.copyfile(input_path, output_path)
                    self.log("ℹ️ Orijinal zaten optimal — orijinal byte'lar korundu.")
            except OSError:
                pass
            self.log_savings(input_path, output_path)
            return output_path

        if tab == "favicon":
            sizes = s["favicon"]["sizes"]
            sizes_str = ",".join(str(x) for x in sizes)
            ico_name = f"{name}_favicon.ico" if multi else "favicon.ico"
            output_path = self._unique_path(os.path.join(out_dir, ico_name), used)
            self.log(f"🎯 Favicon oluşturuluyor → {sizes_str} px")

            # Kareye getir (bozulmasın) — bu görselin büyük kenarını baz al
            size = self._image_size(input_path)
            cmd = ["magick", input_path, "-auto-orient", "-background", "none", "-alpha", "on"]
            if size:
                sq = max(size)
                cmd += ["-gravity", "center", "-extent", f"{sq}x{sq}"]
            cmd += ["-define", f"icon:auto-resize={sizes_str}", output_path]

            if self._run(cmd) == 0:
                self.log(f"🎉 {os.path.basename(output_path)} hazır — "
                         f"{len(sizes)} çözünürlük: {sizes_str}")
                return output_path
            return None

        self.log(f"❌ Unknown tab: {tab}")
        return None

    def _finish_batch(self, results, failures):
        """Ana iş parçacığında: butonu geri getirir, özeti yazar, klasörü açar."""
        self.is_processing = False
        try:
            self.btn_start.configure(state="normal", text="🚀 PROCESS SELECTED TAB")
        except Exception:
            pass

        total = len(results) + len(failures)
        self.log("=" * 60)
        if not results:
            self.log("❌ Nothing could be processed — see the messages above.")
            try:
                messagebox.showerror("Error", "Processing failed.\nSee the Studio Terminal for details.")
            except Exception:
                pass
            return

        if total == 1:
            self.log("🎉 OPERATION COMPLETED FLAWLESSLY!")
        else:
            self.log(f"🎉 {len(results)}/{total} files completed"
                     + (f"  ·  ❌ {len(failures)} failed" if failures else ""))
            for f in failures:
                self.log(f"   ❌ {os.path.basename(f)}")

        self._reveal_in_explorer(results[-1])
        try:
            winsound.PlaySound(r"C:\Windows\Media\notify.wav", winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            pass

        folder = os.path.dirname(os.path.abspath(results[-1]))
        try:
            if total == 1:
                messagebox.showinfo("Success", "Process completed successfully!\n\nSaved to:\n"
                                    f"{os.path.basename(results[0])}")
            elif failures:
                messagebox.showwarning("Finished with errors",
                                       f"{len(results)} of {total} files processed.\n"
                                       f"{len(failures)} failed — see the Studio Terminal.\n\n"
                                       f"Saved to:\n{folder}")
            else:
                messagebox.showinfo("Success", f"All {total} files processed successfully!\n\n"
                                    f"Saved to:\n{folder}")
        except Exception:
            pass

    def _reveal_in_explorer(self, path):
        try:
            subprocess.Popen(f'explorer /select,"{os.path.abspath(path)}"')
        except Exception as e:
            self.log(f"Folder could not be opened: {e}")

    # --- ENCODING ENGINE ---
    def _run(self, cmd):
        """Bir komutu çalıştırır, çıktısını terminale akıtır, dönüş kodunu verir."""
        try:
            self.log(f"⚙️ {' '.join(str(c) for c in cmd)}")
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                # Açık encoding şart: aksi halde Windows ANSI kod sayfası (Türkçe'de
                # cp1254) kullanılır ve harici aracın ürettiği bir bayt çözülemeyince
                # UnicodeDecodeError tüm işi çökertir.
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            for line in process.stdout:
                line = line.strip()
                if line:
                    self.log(line)
            process.wait()
            return process.returncode
        except FileNotFoundError:
            self.log(f"❌ ERROR: '{cmd[0]}' command not found!")
            return 127
        except OSError as e:
            # Aracı başlatırken/okurken oluşan diğer hatalar da bir dönüş koduna
            # çevrilmeli; aksi halde çağıran yerlerdeki geçici dosyalar sızıyor.
            self.log(f"❌ ERROR: '{cmd[0]}' çalıştırılamadı: {e}")
            return 126

    def _cleanup(self, path):
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    def _stage1_png(self, input_path, pre_args):
        """ImageMagick ile herhangi bir girdiyi temiz, EXIF-düzeltilmiş, metadata'sız
        ara PNG'ye çözer. Pro kodlayıcılar bu kayıpsız PNG'yi okur. Başarısızsa None."""
        fd, tmp = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        cmd = ["magick", input_path, "-auto-orient"] + list(pre_args) + ["-strip", tmp]
        try:
            rc = self._run(cmd)
            if rc == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                return tmp
        except Exception:
            # Beklenmedik bir hata olsa bile geçici dosya diskte kalmamalı.
            self._cleanup(tmp)
            raise
        self._cleanup(tmp)
        return None

    def encode(self, input_path, output_path, target_fmt, quality, web_optimize,
               pre_args=None, png_quality="65-90"):
        """Çıktıyı üretir. web_optimize açıkken format başına en iyi kodlayıcıyı
        (MozJPEG / cwebp / pngquant+oxipng) kullanır; araç yoksa ImageMagick'e düşer.
        png_quality, pngquant kalite aralığıdır (ör. '50-80'). Başarıda True döner."""
        pre_args = pre_args or []
        lossy = target_fmt not in self.lossless_formats

        if web_optimize:
            tool = None
            if target_fmt in ("jpg", "jpeg") and self.encoders.get("cjpeg"):
                tool = "jpeg"
            elif target_fmt == "webp" and self.encoders.get("cwebp"):
                tool = "webp"
            elif target_fmt == "png" and (self.encoders.get("pngquant") or self.encoders.get("oxipng")):
                tool = "png"

            if tool:
                tmp = self._stage1_png(input_path, pre_args)
                if tmp:
                    try:
                        if tool == "jpeg":
                            self.log("🚀 MozJPEG (trellis + progressive) ile kodlanıyor...")
                            return self._run(["cjpeg", "-quality", quality, "-optimize",
                                              "-progressive", "-outfile", output_path, tmp]) == 0
                        if tool == "webp":
                            self.log("🚀 Google cwebp (m6 + sharp_yuv) ile kodlanıyor...")
                            return self._run(["cwebp", "-q", quality, "-m", "6", "-sharp_yuv",
                                              "-mt", "-quiet", tmp, "-o", output_path]) == 0
                        if tool == "png":
                            return self._encode_png(tmp, output_path, png_quality)
                    finally:
                        self._cleanup(tmp)
                self.log("⚠️ Pro kodlayıcı ön-işlemi başarısız, ImageMagick'e dönülüyor.")

        # --- IMAGEMAGICK YOLU (varsayılan / yedek) ---
        cmd = ["magick", input_path, "-auto-orient"] + list(pre_args)
        if web_optimize:
            cmd.append("-strip")
            if target_fmt in ("jpg", "jpeg"):
                cmd.extend(["-interlace", "Plane", "-sampling-factor", "4:2:0"])
        if lossy:
            cmd.extend(["-quality", quality])
        cmd.append(output_path)
        return self._run(cmd) == 0

    def _encode_png(self, tmp_png, output_path, png_quality="65-90"):
        """PNG için: önce görsel-kayıpsız pngquant; tutmazsa kayıpsız oxipng;
        o da yoksa ara PNG'yi taşı. PNG çıktısı asla başarısız olmaz."""
        if self.encoders.get("pngquant"):
            self.log(f"🚀 pngquant (görsel-kayıpsız palet, kalite {png_quality}) ile kodlanıyor...")
            rc = self._run(["pngquant", f"--quality={png_quality}", "--strip", "--force",
                            "--speed", "1", "--output", output_path, tmp_png])
            if rc == 0:
                return True
            self.log("ℹ️ pngquant kalite tabanını tutturamadı → kayıpsız oxipng deneniyor.")
        if self.encoders.get("oxipng"):
            self.log("🚀 oxipng (gerçek kayıpsız) ile kodlanıyor...")
            if self._run(["oxipng", "-o", "max", "--strip", "safe",
                          "--out", output_path, tmp_png]) == 0:
                return True
        try:
            shutil.copyfile(tmp_png, output_path)
            return True
        except OSError as e:
            self.log(f"❌ PNG yazılamadı: {e}")
            return False

    def _human(self, n):
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024 or unit == "GB":
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024

    def log_savings(self, input_path, output_path):
        try:
            i = os.path.getsize(input_path)
            o = os.path.getsize(output_path)
        except OSError:
            return
        if i <= 0:
            return
        pct = (1 - o / i) * 100
        if pct >= 0:
            self.log(f"📦 {self._human(i)} → {self._human(o)}  (💾 %{pct:.0f} küçüldü)")
        else:
            self.log(f"📦 {self._human(i)} → {self._human(o)}  (⚠️ %{abs(pct):.0f} büyüdü)")


if __name__ == "__main__":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        # NOT: SetCurrentProcessExplicitAppUserModelID bilinçli olarak ayarlanmıyor.
        # Özel bir AppUserModelID ayarlandığında Windows görev çubuğu, pencere ikonu
        # yerine o AppID için (kayıtlı kısayol yoksa önbellekte boş kalan) ikonu
        # kullanıyor ve boş beyaz sayfa görünüyordu. AppID olmadan görev çubuğu,
        # exe'nin gömülü ikonunu + WM_SETICON pencere ikonunu kullanır.
    except Exception:
        pass

    app = UltimateImageStudio()
    app.mainloop()