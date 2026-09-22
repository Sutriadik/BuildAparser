# Open ADE — image untuk menguji mesin OCR di luar macOS (Linux / Windows-WSL2).
#
# Satu berkas untuk dua kebutuhan, dipilih lewat build-arg -- bukan dua Dockerfile yang
# harus disamakan manual tiap kali berubah:
#
#   CPU (bisa di mana saja, termasuk Windows tanpa GPU):
#     docker build -t openade:cpu .
#
#   GPU NVIDIA (ini alasan utama pindah ke Windows: PaddlePaddle tidak punya backend
#   Metal, jadi di Mac ia selalu CPU. Ambil nama image & perintah wheel yang TEPAT dari
#   halaman instalasi resmi PaddlePaddle -- versi CUDA berubah-ubah, jadi sengaja tidak
#   dipatok di sini supaya tidak menyesatkan):
#     docker build -t openade:gpu \
#       --build-arg BASE_IMAGE=nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04 \
#       --build-arg PADDLE_PKG=paddlepaddle-gpu \
#       --build-arg PADDLE_INDEX=https://www.paddlepaddle.org.cn/packages/stable/cu126/ .
ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

ARG PADDLE_PKG=paddlepaddle
ARG PADDLE_INDEX=

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# libgl1 + libglib2.0-0 : dibutuhkan OpenCV (dipakai PaddleOCR & EasyOCR).
# libgomp1             : runtime OpenMP untuk onnxruntime/paddle.
# tesseract-ocr-ind    : Tesseract dipanggil sebagai BINER lewat CLI, bukan paket pip,
#                        jadi bahasanya harus dipasang di level sistem. Tanpa paket
#                        'ind' teks Indonesia berimbuhan banyak yang salah baca.
# python3/pip          : hanya terpakai bila BASE_IMAGE-nya bukan image python resmi
#                        (mis. base CUDA), makanya dipasang dengan '|| true' di bawah.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libgomp1 \
        tesseract-ocr tesseract-ocr-ind tesseract-ocr-eng \
        ca-certificates \
    && (command -v python3 >/dev/null || apt-get install -y --no-install-recommends python3 python3-pip) \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements disalin duluan supaya layer pip tidak ikut terbangun ulang tiap kali
# kode berubah -- instalasi torch/paddle di sini memakan waktu paling lama.
COPY requirements.txt .

# paddlepaddle dari requirements dilepas dulu, lalu dipasang sesuai varian yang diminta.
# Wheel CPU dan GPU tidak boleh terpasang bersamaan: yang belakangan menang diam-diam
# dan hasilnya sulit dilacak.
RUN sed -i '/^paddlepaddle/d' requirements.txt \
    && python3 -m pip install --upgrade pip \
    && python3 -m pip install -r requirements.txt \
    && if [ -n "$PADDLE_INDEX" ]; then \
           python3 -m pip install "$PADDLE_PKG" -i "$PADDLE_INDEX"; \
       else \
           python3 -m pip install "$PADDLE_PKG"; \
       fi

# sample_pdfs/ dikecualikan lewat .dockerignore (kontrak asli tidak ikut ke dalam
# image); di docker-compose.yml folder itu dipasang sebagai bind mount read-only.
COPY . .

# Model OCR (RapidOCR/EasyOCR/Paddle) diunduh saat pertama dipakai. Folder-folder ini
# dijadikan volume di docker-compose.yml supaya unduhan itu tidak terulang tiap build.
ENV EASYOCR_MODULE_PATH=/models/easyocr \
    PADDLE_PDX_CACHE_HOME=/models/paddle \
    HF_HOME=/models/hf \
    OCR_ENGINE=rapidocr \
    LOG_LEVEL=INFO

# Default: cek mesin OCR apa saja yang hidup di image ini, bukan langsung menjalankan
# pipeline. Container yang mati tanpa pesan jelas lebih sulit didiagnosis daripada
# container yang bilang "engine X tidak tersedia".
CMD ["python3", "cek_ocr.py"]
