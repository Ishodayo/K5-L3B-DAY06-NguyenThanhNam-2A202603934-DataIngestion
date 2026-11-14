# Day 06 · Data Ingestion Engineering

**Lab cá nhân** · **4 giờ** · **Dữ liệu tự chọn**

Biến tài liệu thành kho tri thức có thể truy xuất cho AI agent, rồi đo xem cách chuẩn bị dữ liệu nào hiệu quả nhất.

| 📄 Dữ liệu | 🧩 Chiến lược | 🎯 Benchmark |
|---|---|---|
| Tối thiểu **30 trang nội dung** | Tối thiểu **3 cấu hình** | Tối thiểu **20 câu hỏi** |

## Cách chạy bài làm (Nguyễn Thành Nam · 2A202603934)

| Thành phần | Đường dẫn / giá trị |
|---|---|
| Corpus | `data/thinkpython2.pdf` — *Think Python 2e*, CC BY-NC 3.0; manifest: `data/manifest.json` (203/244 trang được tính) |
| Benchmark | `benchmark/questions.jsonl` (22 câu: 16 dev / 6 test), cách chia và tiêu chí chọn: `benchmark/README.md` |
| Code | `starter/src/` (`ingestion`, `chunking`, `embeddings`, `store`, `evaluation`, `audit`, `run`) |
| Kết quả | `results/` (chunk/ingest stats, trace, `metrics.csv`, `selection.json`, `audit/`), `artifacts/experiment_log.csv` |
| Báo cáo | `report/individual/2A202603934_NguyenThanhNam.md` |
| Môi trường | Python 3.15.2, pypdf 7.1.0, sentence-transformers 6.3.0 (`BAAI/bge-base-en-v1.5`, 768d, CPU), PostgreSQL 16.16 + pgvector 0.8.7 (Docker); đầy đủ: `results/requirements-lock.txt` |

Các lệnh chạy từ thư mục `starter/` (Windows PowerShell; macOS/Linux dùng `.venv/bin/python`):

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt sentence-transformers==6.3.0
Copy-Item .env.example .env                          # EMBEDDING_MODEL để trống = BAAI/bge-base-en-v1.5
docker compose -p day6_student_starter up -d --wait
.venv\Scripts\python scripts\check_setup.py          # Setup OK, pgvector 0.8.7
.venv\Scripts\python -m src.run chunk                # đọc 203 trang, chunk A (929) / B (528)
.venv\Scripts\python -m src.run ingest               # embed + lưu 3 index A/B/C (~8 phút CPU lần đầu; lần sau chỉ embed chunk đổi)
.venv\Scripts\python -m src.run search "How do I open a file for writing?" --chapter ch14
.venv\Scripts\python -m src.run eval --split dev     # Hit@1/Hit@3 dev cho A/B/C
.venv\Scripts\python -m src.run select               # chọn chiến lược từ dev (đã chạy -> C; từ chối chọn lại sau khi test đã chạy)
.venv\Scripts\python -m src.run eval --split test    # test chỉ chạy một lần (đã chạy; lệnh sẽ từ chối chạy lại)
.venv\Scripts\python -m src.run audit --summary      # tổng hợp nhãn coherence / metadata / grounding
```

Lần đầu chạy cần mạng để tải model (~419 MB) và image Docker. Không cần API key; chi phí 0.

## Bắt đầu nhanh

Dùng [starter PostgreSQL + pgvector](starter/README.md) để dựng database và hoàn thiện các hàm TODO, hoặc tự chọn vector store tương đương. Starter chỉ là khung: **bạn tự hiện thực pipeline và benchmark**.

## 1. Xây pipeline

![Đọc tài liệu, chuẩn hóa, chia chunk, tạo vector, lưu và kiểm tra](assets/ingestion-pipeline.svg)

Chọn tài liệu được phép sử dụng; giữ **ID tài liệu, nguồn, trang/vị trí** và metadata phù hợp. Dùng embedding model thật qua API hoặc chạy local; lưu và truy xuất bằng pgvector, Qdrant, Chroma hoặc một store tương đương.

## 2. So sánh và đánh giá

![Ba chiến lược được đánh giá trên cùng corpus và benchmark dev/test](assets/benchmark-workflow.svg)

Dùng cùng dữ liệu, model và câu hỏi cho cả ba cấu hình. Tách **15 câu dev / 5 câu test** trước khi chạy; chọn cấu hình trên dev, rồi đánh giá test sau khi cố định. Mỗi câu hỏi cần có đoạn bằng chứng và trang nguồn; thiết kế bộ câu hỏi và đánh giá theo [BENCHMARK.md](BENCHMARK.md).

Đánh giá theo năm khía cạnh: **độ chính xác truy xuất**, **tính mạch lạc của chunk**, **tính hữu dụng của metadata**, **chất lượng grounding** và **tác động của chiến lược dữ liệu**. Giải thích lựa chọn bằng kết quả, ví dụ lỗi và các đánh đổi.

## 3. Chuẩn bị bài nộp

1. Chạy pipeline và lưu cấu hình, kết quả top-3, trace cùng nhãn đánh giá thủ công.
2. Điền [thông tin học viên](STUDENT.md), [nhật ký thí nghiệm](artifacts/experiment_log.csv) và bản sao [mẫu báo cáo cá nhân](report/individual/MSSV_HoTen.md).
3. Kiểm tra mã nguồn, cách chạy, manifest dữ liệu và checklist trong [SUBMISSION.md](SUBMISSION.md).

## Tài liệu hướng dẫn

| Bạn cần tìm | Tài liệu |
|---|---|
| Setup baseline PostgreSQL + pgvector | [starter/README.md](starter/README.md) |
| Các bước làm và cách tính chỉ số | [GUIDE.md](GUIDE.md) |
| Tiến độ trong 4 giờ | [CHECKPOINTS.md](CHECKPOINTS.md) |
| Năm tiêu chí benchmark và bộ câu hỏi | [BENCHMARK.md](BENCHMARK.md) |
| Tiêu chí chấm điểm | [RUBRIC.md](RUBRIC.md) |
| Quy tắc sử dụng dữ liệu, AI và API | [RULES.md](RULES.md) |
| Cách nộp bài | [SUBMISSION.md](SUBMISSION.md) |

[data/format-example.json](data/format-example.json) chỉ minh họa định dạng, không phải corpus đủ điều kiện. Phần xử lý và benchmark do bạn tự hiện thực; không bắt buộc xây chatbot.
