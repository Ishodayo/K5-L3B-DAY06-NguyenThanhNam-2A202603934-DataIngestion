# Báo cáo cá nhân — Day 6

Nguyễn Thành Nam · 2A202603934 · L3B · chạy ngày 2026-11-14

## Dữ liệu và pipeline
- **Chủ đề, nguồn/quyền, số trang và quy tắc tính trang:** *Think Python 2e* (Allen B. Downey), 1 PDF tiếng Anh, <https://greenteapress.com/thinkpython2/thinkpython2.pdf>, giấy phép CC BY-NC 3.0. 244 trang vật lý, **203 trang được tính**: PDF 23–232 (Chương 1–19, Phụ lục A–B), loại bìa/mục lục/lời nói đầu (1–22), chỉ mục (233–244) và 7 trang < 300 ký tự. Chi tiết: [`data/manifest.json`](../../data/manifest.json).
- **Chuẩn hóa, lỗi và giới hạn:** pypdf 7.1.0 (đổi từ 6.21.4 vì bản 6 làm dính chữ quanh chữ đậm/code); NFC, bỏ header đầu trang, nối gạch nối cuối dòng, gộp khoảng trắng. 203/203 trang đọc được, 0 rỗng, 0 lỗi, không có trang scan ([`ingest_log.txt`](../../results/ingest_log.txt)). Giới hạn: code mất thụt lề, hình vẽ trích ra thành chuỗi số rời.
- **Embedding, vector store, metric, lệnh chạy, chi phí:** `BAAI/bge-base-en-v1.5` (Hugging Face, revision `a5beb1e3`, chạy local bằng sentence-transformers 6.3.0), 768 chiều, max 512 token (0 chunk vượt), vector chuẩn hóa; query thêm câu hướng dẫn BGE, document không. PostgreSQL 16.16 + pgvector 0.8.7 (Docker `pgvector/pgvector:0.8.7-pg16`, volume `pg_data`), bảng `chunks` (cột `strategy` tách 3 index A/B/C, tạo bằng `ingest`), cosine, exact search. Lệnh chạy: mục *Cách chạy bài làm* trong [`README.md`](../../README.md); phiên bản thư viện: [`requirements-lock.txt`](../../results/requirements-lock.txt). Chi phí 0 USD. Công cụ: Python 3.15.2, pypdf, sentence-transformers, PostgreSQL/pgvector, Docker; có dùng AI (Claude) gợi ý; kiểm tra bằng chạy lại lệnh và đối chiếu với PDF.
- **Metadata:** bắt buộc `document_id` (str), `source` (str, URL), `page` (int, số trang PDF). Trường lọc `chapter_id` (str: `ch01`–`ch19`, `appendix_A/B`) và `chapter_title` (str) lấy từ bookmark PDF; thêm `section` (str, heading gần nhất trong text). Lý do: mục Debugging/Glossary và thuật ngữ (vd *hashable*) lặp ở nhiều chương, lọc theo chương giữ đúng phạm vi; `page` giúp truy vết đúng trang. Hạn chế: `chapter_id` chỉ lọc được cả chương (8–14 trang), không giúp khi người dùng không biết chương; `page` là một trang nên chunk không được vượt trang.

## Cấu hình và benchmark
- **A / B / C:**

  | | Chunking | Tham số | Truy xuất |
  |---|---|---|---|
  | A | cửa sổ ký tự cố định, trong từng trang | 500, overlap 50 | top-3, không lọc |
  | B | theo heading mục + đoạn văn, cắt ở ranh giới câu, trong từng trang | tối đa 1.200 ký tự | top-3, không lọc |
  | C | giống B (cùng chunk và vector) | như B | top-3, lọc `chapter_id` chỉ ở câu có filter |

  B–C chỉ khác filter; A–B chỉ khác chunking. Cấu hình: [`experiment_log.csv`](../../artifacts/experiment_log.csv).
- **Benchmark:** 22 câu, **16 dev / 6 test**, chia theo chương trước khi chạy ([`questions.jsonl`](../../benchmark/questions.jsonl)). 5 câu filter (dev 3, test 2); filter là chương do người dùng cung cấp trước khi truy xuất. 27/27 evidence (gồm 5 trang thay thế) được kiểm tra có trong trang gold sau chuẩn hóa.
- **Tiêu chí chọn (chốt trước test, [`benchmark/README.md`](../../benchmark/README.md)):** Hit@3 dev → nếu chênh ≤ 1 câu thì Hit@1 dev → ít chunk/không filter; chọn C thay B nếu không giảm Hit@3 câu không filter và tuân thủ filter 3/3. Test chạy một lần.

## Kết quả
| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian | Chi phí |
|---|---|---|---|---|---|---|
| A | dev | 9/16 | 12/16 | 929 | embed 242,2 s | 0 |
| A | test | 3/6 | 5/6 | 929 | | 0 |
| B | dev | 11/16 | 13/16 | 528 | embed 189,9 s | 0 |
| B | test | 4/6 | 4/6 | 528 | | 0 |
| **C** | dev | 12/16 | 13/16 | 528 | 0 s (vector của B) | 0 |
| **C** | test | 4/6 | 4/6 | 528 | | 0 |

Độ dài chunk (ký tự): A min/median/max 51/500/500, tổng 416.093; B 54/749/1.200, tổng 379.717 ([`chunk_stats.json`](../../results/chunk_stats.json)). Ingest đầy đủ 468,5 s (CPU). Nạp lại 4 lần: 0 thêm/sửa/xóa, số dòng giữ 929/528/528 ([`reingest_check.txt`](../../results/reingest_check.txt)); tài liệu đổi được xử lý bằng `chunk_id` ổn định + `content_hash`.

| Strategy | Coherence trung bình /2 (n) | Metadata đủ và đúng (n/N) | Filter tuân thủ (n/N hoặc N/A) | Grounding 0/1/2 (số câu) và trung bình /2 |
|---|---|---|---|---|
| A | 0,90 (10) | 18/18 | 1/2 | 0/1/5 — 1,83 |
| B | 1,90 (10) | 18/18 | 1/2 | 1/1/4 — 1,50 |
| C | 1,90 (10) | 18/18 | 2/2 | 2/0/4 — 1,33 |

Đường dẫn trace, nhãn thủ công và cách chọn mẫu: trace [`traces_A/B/C.jsonl`](../../results/), [`metrics.csv`](../../results/metrics.csv), [`selection.json`](../../results/selection.json); nhãn ở [`results/audit/`](../../results/audit/). Coherence: câu đầu tiên sau điểm giữa trang của 10 trang đầu (PDF 23–32), chọn trước khi chấm. Metadata: 6 câu test × top-3. Grounding: 6 câu test, xét cả top-3. Người chấm: tác giả, có nguy cơ thiên lệch.

## Phân tích và kết luận
- **A–B:** B giữ trọn ý (coherence 1,90 so với 0,90; evidence dev nằm trọn trong 1 chunk: B 16/16, A 14/16 — d07, d10 bị tách qua 2 chunk A) và nhỉnh hơn trên dev. Trên test A hơn (Hit@3 5/6 so với 4/6) vì chunk B dài, lẫn code (752–1.178 ký tự ở d09, d15, t02) làm loãng vector với câu hỏi định nghĩa ngắn.
- **B–C:** C giữ top-3 đúng chương ở mọi câu filter (dev 3/3, test 2/2; B 0/3, 1/2), Hit@1 dev 11 → 12, Hit@3 không đổi.
- **Ví dụ sai:**
  - **d07** (PDF 96, ch08), A: cửa sổ 500 ký tự cắt evidence thành `…can’t change an exist` | `riginal string`; đúng trang nhưng không chunk nào đủ evidence. B/C lấy trọn mục 8.5.
  - **t05** (PDF 229, appendix_B), cả 3 trượt: A/B kéo Glossary ch11 trang 134 (ngoài phạm vi); C lọc đúng appendix_B nhưng trang 229 không vào top-3.
- **Chiến lược chọn:** **C**, theo tiêu chí đã chốt trên dev; không đổi sau khi thấy test. Đánh đổi: chunk dễ đọc, đúng phạm vi chương; nhưng chunk dài kém A ở câu định nghĩa ngắn, và filter bỏ bằng chứng ở chương khác. Phù hợp khi người dùng biết mình đang hỏi trong chương nào.
- **Hạn chế:** test 6 câu (1 câu = 16,7 điểm %), dev 16 câu; một sách, câu hỏi do tác giả viết; một người chấm; thời gian đo một lần trên CPU, chi phí 0 vì chạy local; 42/528 chunk B dừng giữa câu do không vượt trang.
- **Cải tiến:** giảm giới hạn chunk B (600–800 ký tự) hoặc tách code khỏi văn bản; thử hybrid dense + BM25; cho chunk nối trang và lưu danh sách trang; mở rộng tập test.
