# Georgian State Procurement 24/7 Monitor

Automated 24/7 monitoring and Telegram notification system for newly published **Tenders (ტენდერები)** and **Market Researches (ბაზრის კვლევები)** on [`tenders.procurement.gov.ge`](https://tenders.procurement.gov.ge/public/?lang=ge), filtered strictly by your configured CPV codes.

## Features
- **Tenders & Market Researches**: Monitors both `შესყიდვები` (`NAT`, `SPA`, `CON`, `GEO`, etc.) and `ბაზრის კვლევა` (`MRS`).
- **Strict CPV Filtering**: Batched queries plus exact detail-page CPV verification using `CPV_CODES`.
- **Deep Delivery Term Extraction**: Reads main summary, falls back to the `დოკუმენტაცია` tab (section 1.4 & technical terms), and parses attached `.pdf` (`pypdf`) and `.docx` (`python-docx`) files when needed.
- **Zero-Duplicate Persistence**: Stores sent `განცხადების ნომერი` IDs in `state.json`, automatically committed back to the repository by GitHub Actions.
- **24/7 Cloud Execution**: Runs every 5 minutes via `.github/workflows/monitor.yml`.

## Configuration (GitHub Secrets / `.env`)
- `BOT_TOKEN`: Telegram Bot API token
- `CHAT_ID`: Target Telegram chat/group/channel ID
- `CPV_CODES`: Comma-, space-, or newline-separated 8-digit CPV codes

## Local Run
```bash
pip install -r requirements.txt
cp .env.example .env
python main.py
```
