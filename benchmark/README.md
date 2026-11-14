# Benchmark — Think Python 2e

Chốt ngày **2026-11-14**, **trước khi** chạy bất kỳ chiến lược truy xuất nào. Không sửa câu/nhãn test sau khi xem kết quả; nếu buộc phải sửa sẽ ghi lại ở đây và không coi test là holdout độc lập.

## File
- `questions.jsonl` — 22 câu, mỗi dòng một JSON.

| Trường | Ý nghĩa |
|---|---|
| `id` | `dNN` = dev, `tNN` = test |
| `split` | `dev` / `test` |
| `type` | `direct`, `paraphrase`, `condition_exception`, `metadata_filter` |
| `question`, `expected_answer` | câu hỏi (tiếng Anh, cùng ngôn ngữ corpus) và đáp án |
| `gold_source_id` | `thinkpython2` (xem `data/manifest.json`) |
| `gold_page` | **số trang PDF** (1-based), không phải số trang in (= trang PDF − 22) |
| `gold_chapter_id` | chương của trang gold — chỉ dùng phân tích lỗi, **không** đưa vào query/filter |
| `evidence_quote` | đoạn văn nguyên văn trong trang gold, đủ để trả lời |
| `filter` | `{"chapter_id": ...}` hoặc `null` |
| `alternatives` | các cặp `gold_page` + `evidence_quote` khác cũng hợp lệ (vd mục Glossary); trúng bất kỳ cặp nào đều tính hit |

## Thống kê
| | dev | test | tổng |
|---|---:|---:|---:|
| Số câu | 16 | 6 | 22 |
| `direct` | 4 | 1 | 5 |
| `paraphrase` | 4 | 2 | 6 |
| `condition_exception` | 5 | 1 | 6 |
| `metadata_filter` | 3 | 2 | 5 |
| Có `alternatives` | 3 | 2 | 5 |

## Cách chia dev/test
Chia **theo chương**, chốt trước khi chạy: câu test chỉ lấy từ các chương không có câu dev nào.
- Dev: ch02, ch05, ch06, ch08, ch10, ch11, ch13, ch14, ch15, appendix_A
- Test: ch12, ch16, ch17, ch18, ch19, appendix_B

Test có 6 câu (yêu cầu ≥ 5), dư 1 câu để giảm phụ thuộc vào từng câu riêng lẻ.

**Nguy cơ rò rỉ / thiên lệch**
- Cùng một cuốn sách, cùng tác giả và văn phong → dev và test không độc lập hoàn toàn về chủ đề (vd `hashable` xuất hiện ở ch11 (dev) và appendix_B (test)).
- Câu hỏi do chính học viên viết sau khi đọc trang gold → có thể vô tình dùng từ trùng với evidence, làm Hit cao hơn thực tế. Câu `paraphrase` được viết lại để giảm trùng từ.
- Test nhỏ (6 câu): mỗi câu = 16,7 điểm %; chỉ dùng để minh họa, không kết luận tổng quát.

## Metadata filter
- Trường lọc: `chapter_id` (`ch01`…`ch19`, `appendix_A`, `appendix_B`), lấy từ bookmark của PDF (xem manifest).
- Giả định: filter là **input do người dùng cung cấp trước khi truy xuất** (người học đang đọc một chương cụ thể); câu hỏi nêu rõ chương bằng lời. Filter là phạm vi cả chương (8–14 trang), không phải trang gold.
- Câu filter được chọn vì thuật ngữ xuất hiện ở nhiều chương: debugging tips (d11, d12, d16 — chương nào cũng có mục Debugging), AttributeError (ch15 và appendix_A), hashable keys (ch11 và appendix_B), class attribute (ch18).
- Cấu hình C chỉ áp dụng filter cho 5 câu có `filter`; 17 câu còn lại chạy không lọc (giống B).

## Kiểm chứng evidence
Đã kiểm tra tự động cả 27 đoạn evidence (22 chính + 5 thay thế): mỗi đoạn có trong text của đúng trang gold sau chuẩn hóa; giá trị filter có trong manifest và khớp chương của trang gold; không chương nào vừa ở dev vừa ở test. Kết quả: **27/27 đạt**, với cả pypdf 6.21.4 và 7.1.0.

Quy tắc so khớp (dùng lại khi chấm Evidence Hit): Unicode NFC → bỏ gạch nối cuối dòng → bỏ **toàn bộ** khoảng trắng → chữ thường, rồi kiểm tra chuỗi con. Lý do: pypdf 6.x làm mất khoảng trắng quanh chữ in đậm/monospace (vd `Thefloor divisionoperator`), nên so khớp theo khoảng trắng sẽ báo sai.

## Tiêu chí chọn chiến lược (chốt trước khi xem test)
1. Evidence Hit@3 trên **dev** cao nhất.
2. Nếu chênh ≤ 1 câu (1/16): Hit@1 trên dev.
3. Nếu vẫn hòa: chiến lược đơn giản hơn / ít chunk hơn / ingest nhanh hơn.
4. Riêng B so với C: chọn C nếu C không làm giảm Hit@3 ở các câu không filter **và** tuân thủ filter ở 3/3 câu filter dev.

Test chỉ chạy **một lần** với cấu hình đã cố định theo dev; kết quả test được báo cáo nhưng không dùng để chỉnh tham số.
