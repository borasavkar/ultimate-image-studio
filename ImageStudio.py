import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import subprocess
import threading
import os
import sys
import winsound
import time
import tempfile
import shutil

try:
    from PIL import Image, ImageOps
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

try:
    from rembg import remove
    HAS_REMBG = True
    REMBG_ERROR = ""
except Exception as e:
    HAS_REMBG = False
    REMBG_ERROR = str(e)

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
        self.title("Ultimate Image Studio Pro v1.5 - Smart UI & Filters")
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
        
        ctk.CTkLabel(inner_files, text="Input Image:").grid(row=0, column=0, padx=15, pady=10, sticky="w")
        self.entry_img = ctk.CTkEntry(inner_files, textvariable=self.input_file, placeholder_text="Select image to process...")
        self.entry_img.grid(row=0, column=1, sticky="ew", padx=10, pady=10)
        ctk.CTkButton(inner_files, text="Browse", width=100, command=self.select_image).grid(row=0, column=2, padx=10)

        ctk.CTkLabel(inner_files, text="Output Folder:").grid(row=1, column=0, padx=15, pady=(0, 15), sticky="w")
        self.entry_out = ctk.CTkEntry(inner_files, textvariable=self.output_dir, placeholder_text="Select destination folder...")
        self.entry_out.grid(row=1, column=1, sticky="ew", padx=10, pady=(0, 15))
        ctk.CTkButton(inner_files, text="Browse", width=100, command=self.select_output_dir).grid(row=1, column=2, padx=10, pady=(0, 15))

        # 2. STUDIO TABS
        self.tabview = ctk.CTkTabview(self, command=self.on_tab_change)
        self.tabview.grid(row=1, column=0, sticky="nsew", padx=15, pady=5)

        self.tabs = {}

        self.create_convert_tab("🔄 Format Conversion")
        self.create_optimize_tab("🗜️ Web Optimize")
        self.create_resize_tab("📐 Image Resizing")
        self.create_ai_tab("🪄 AI Background Removal")

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

        self.on_tab_change()
        self._log_encoder_status()

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
                
        # Dışarıdan UI tetiklemek için fonksiyonu hafızaya al
        self.tabs[tab_name]["update_ui_func"] = update_quality_ui

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
                
        # Dışarıdan UI tetiklemek için fonksiyonu hafızaya al
        self.tabs[tab_name]["update_ui_func"] = update_quality_ui_resize

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

        seg = ctk.CTkSegmentedButton(
            frame, values=["Low", "Medium", "High"], variable=vars["level"],
            command=on_level, selected_color="#27ae60", selected_hover_color="#1e8449")
        seg.grid(row=3, column=0, columnspan=2, padx=15, pady=(0, 6), sticky="w")

        lbl_desc.grid(row=4, column=0, columnspan=2, padx=15, pady=(2, 6), sticky="w")
        on_level("Medium")

        ctk.CTkLabel(frame, text="Output:  <name>_optimized.<same extension>   ·   Never larger than the original.",
                     text_color="#777777", font=("Arial", 11)).grid(
                     row=5, column=0, columnspan=2, padx=15, pady=(12, 4), sticky="w")

    def create_ai_tab(self, tab_name):
        self.tabview.add(tab_name)
        frame = self.tabview.tab(tab_name)

        ctk.CTkLabel(frame, text="🤖 Powered by U^2-Net Artificial Intelligence", font=("Arial", 14, "bold"), text_color="#8e44ad").pack(pady=20)
        ctk.CTkLabel(frame, text="This tool automatically detects the main subject and removes the background.\nThe output will be strictly saved as a transparent .png file.").pack(pady=10)

# --- FILE HANDLING ---
    def select_image(self):
        path = filedialog.askopenfilename(
            title="Select Image",
            filetypes=[("Image Files", "*.png *.jpg *.jpeg *.webp *.avif *.heic *.bmp *.jxl"), ("All Files", "*.*")]
        )
        if path:
            self.input_file.set(path)
            
            # YENİ: Resim her seçildiğinde Output klasörünü ACIMASIZCA o klasöre eşitle
            yeni_klasor = os.path.dirname(path)
            self.output_dir.set(yeni_klasor)
            
            self.log(f"Image Selected: {os.path.basename(path)}")
            self.log(f"📂 Output Folder Auto-Set: {yeni_klasor}")
            
            # --- OTOMATİK FORMAT TESPİTİ VE UI GÜNCELLEMESİ ---
            _, ext = os.path.splitext(path)
            ext = ext.lower().replace(".", "")
            if ext == "jpeg": ext = "jpg" # Normalizasyon
            
            if ext in self.supported_formats:
                self.log(f"🎨 Source format auto-detected: {ext.upper()}")
                
                # Conversion Sekmesini Otopilota Al
                self.tabs["🔄 Format Conversion"]["target_format"].set(ext)
                if "update_ui_func" in self.tabs["🔄 Format Conversion"]:
                    self.tabs["🔄 Format Conversion"]["update_ui_func"](ext)
                    
                # Resize Sekmesini Otopilota Al
                self.tabs["📐 Image Resizing"]["target_format"].set(ext)
                if "update_ui_func" in self.tabs["📐 Image Resizing"]:
                    self.tabs["📐 Image Resizing"]["update_ui_func"](ext)
            
            # --- PİLLOW İLE ORİJİNAL BOYUTLARI OKUMA ---
            if HAS_PILLOW:
                try:
                    with Image.open(path) as img:
                        # Sihirli Dokunuş: Pillow'a EXIF yönünü okutuyoruz
                        img = ImageOps.exif_transpose(img)
                        
                        self.orig_w, self.orig_h = img.size
                        self.log(f"📐 Original Dimensions detected: {self.orig_w}x{self.orig_h}")
                        
                        self._updating_ratio = True
                        self.tabs["📐 Image Resizing"]["width"].set(str(self.orig_w))
                        self.tabs["📐 Image Resizing"]["height"].set(str(self.orig_h))
                        self._updating_ratio = False
                except Exception as e:
                    self.log(f"⚠️ Dimensions could not be read automatically. You can enter them manually.")

    def select_output_dir(self):
        path = filedialog.askdirectory(title="Select Destination Folder")
        if path:
            self.output_dir.set(path)

    def log(self, message):
        self.txt_log.configure(state='normal')
        self.txt_log.insert(tk.END, message + "\n")
        self.txt_log.see(tk.END)
        self.txt_log.configure(state='disabled')

    def on_closing(self):
        self.destroy()
        os._exit(0)

    # --- CORE PROCESSING ENGINES ---
    def start_thread(self):
        if not self.input_file.get():
            messagebox.showerror("Error", "Please select an input image first!")
            return
        
        if self.is_processing:
            return

        self.btn_start.configure(state="disabled", text="⏳ PROCESSING...")
        self.is_processing = True

        threading.Thread(target=self.process_image, daemon=True).start()

    def process_image(self):
        try:
            active_tab = self.tabview.get()
            input_path = self.input_file.get()
            out_dir = self.output_dir.get()
            
            filename = os.path.basename(input_path)
            name, _ = os.path.splitext(filename)

            self.log("=" * 60)
            self.log(f"🎬 STUDIO ENGINE STARTED: {active_tab}")
            
            if "AI" in active_tab:
                if not HAS_REMBG:
                    self.log(f"❌ ERROR: AI Engine failed to load!\n⚠️ Hidden Detail: {REMBG_ERROR}")
                    return
                    
                output_path = os.path.join(out_dir, f"{name}_NoBG.png")
                self.log("🤖 AI Engine analyzing the image (CPU Mode)...")
                
                with open(input_path, 'rb') as i:
                    input_data = i.read()
                    
                output_data = remove(input_data)
                
                with open(output_path, 'wb') as o:
                    o.write(output_data)
                    
                self.log("✨ AI Background removal successful!")
                self.finish_processing(output_path)

            elif "Conversion" in active_tab:
                vars = self.tabs[active_tab]
                target_fmt = vars["target_format"].get()
                quality = str(int(vars["quality"].get()))

                # Çıktı dosya adına yükseklik (height) değerini ekle.
                # Dönüştürme boyutu değiştirmediği için orijinal yükseklik geçerlidir.
                out_height = self.orig_h
                if out_height is None and HAS_PILLOW:
                    try:
                        with Image.open(input_path) as img:
                            out_height = ImageOps.exif_transpose(img).size[1]
                    except Exception:
                        out_height = None
                # Yükseklik okunamazsa orijinali ezmemek için "_web" yedeği kullanılır.
                height_tag = f"_{out_height}px" if out_height else "_web"

                output_path = os.path.join(out_dir, f"{name}{height_tag}.{target_fmt}")

                web = vars["web_optimize"].get()
                q_label = quality if target_fmt not in self.lossless_formats else 'Lossless'
                self.log(f"🪄 Converting to {target_fmt.upper()} with Quality: {q_label}{' | 🌍 Web Optimize' if web else ''}")
                if self.encode(input_path, output_path, target_fmt, quality, web):
                    self.log_savings(input_path, output_path)
                    self.finish_processing(output_path)

            elif "Resizing" in active_tab:
                vars = self.tabs[active_tab]
                w = vars["width"].get()
                h = vars["height"].get()
                target_fmt = vars["target_format"].get()
                keep_aspect = vars["keep_aspect"].get()
                filter_choice = vars["filter"].get()
                quality = str(int(vars["quality"].get()))
                
                resize_param = f"{w}x{h}" if keep_aspect else f"{w}x{h}!"
                aspect_tag = "KeepAspect" if keep_aspect else "Forced"
                
                magick_filter = filter_choice.split(" ")[0]
                
                output_path = os.path.join(out_dir, f"{name}_Resized_{w}x{h}_{aspect_tag}.{target_fmt}")

                pre_args = []
                if magick_filter != "Auto":
                    pre_args.extend(["-filter", magick_filter])
                pre_args.extend(["-resize", resize_param])

                web = vars["web_optimize"].get()
                q_label = quality if target_fmt not in self.lossless_formats else 'Lossless'
                self.log(f"📏 Resizing to {w}x{h} (Filter: {magick_filter})")
                self.log(f"🪄 Output Format: {target_fmt.upper()}, Quality: {q_label}{' | 🌍 Web Optimize' if web else ''}")
                if self.encode(input_path, output_path, target_fmt, quality, web, pre_args=pre_args):
                    self.log_savings(input_path, output_path)
                    self.finish_processing(output_path)

            elif "Optimize" in active_tab:
                vars = self.tabs[active_tab]
                level = vars["level"].get()
                preset = self.optimize_levels.get(level, self.optimize_levels["Medium"])

                # Formatı KORU: çıktının uzantısı girişle aynı (convert yok).
                ext = os.path.splitext(filename)[1].lower().lstrip(".")
                fmt_key = "jpg" if ext == "jpeg" else ext
                output_path = os.path.join(out_dir, f"{name}_optimized.{ext}")

                self.log(f"🗜️ Web Optimize ({level}) — format korunuyor: .{ext.upper()}")
                if fmt_key == "png":
                    ok = self.encode(input_path, output_path, "png", "0", True,
                                     png_quality=preset["png"])
                else:
                    q = str(preset.get(fmt_key, 75))
                    ok = self.encode(input_path, output_path, ext, q, True)

                if ok:
                    # ImageOptim ilkesi: çıktı asla orijinalden büyük olmasın.
                    try:
                        if os.path.getsize(output_path) >= os.path.getsize(input_path):
                            shutil.copyfile(input_path, output_path)
                            self.log("ℹ️ Orijinal zaten optimal — orijinal byte'lar korundu.")
                    except OSError:
                        pass
                    self.log_savings(input_path, output_path)
                    self.finish_processing(output_path)
        except Exception as e:
            self.log(f"❌ CRITICAL ERROR: {str(e)}")
        finally:
            self.btn_start.configure(state="normal", text="🚀 PROCESS SELECTED TAB")
            self.is_processing = False

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
        rc = self._run(cmd)
        if rc == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            return tmp
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

    def finish_processing(self, output_path):
        self.log("=" * 60)
        self.log(f"🎉 OPERATION COMPLETED FLAWLESSLY!")
        try:
            abs_out_path = os.path.abspath(output_path)
            subprocess.Popen(f'explorer /select,"{abs_out_path}"')
        except Exception as e: 
            self.log(f"Folder could not be opened: {e}")
            
        try: winsound.PlaySound(r"C:\Windows\Media\notify.wav", winsound.SND_FILENAME | winsound.SND_ASYNC)
        except: pass
        
        messagebox.showinfo("Success", f"Process completed successfully!\n\nSaved to:\n{os.path.basename(output_path)}")

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