# Claude Code (cài qua brew) — dùng provider khác, cấu hình theo project

Claude Code mặc định dùng OAuth / đăng nhập Anthropic. Nếu muốn dùng provider khác
(ví dụ OpenRouter), cấu hình qua biến môi trường. Provider phải hỗ trợ
**Anthropic Messages API**.

## 1. Biến môi trường cần thiết

```bash
export ANTHROPIC_BASE_URL="https://openrouter.ai/api"
export ANTHROPIC_AUTH_TOKEN="sk-or-v1-..."
export ANTHROPIC_MODEL="anthropic/claude-sonnet-4"
```

Một số provider dùng chuẩn key của Anthropic thì thay bằng:

```bash
export ANTHROPIC_API_KEY="..."
```

Thoát OAuth nếu mỗi lần mở đều bắt đăng nhập:

```bash
claude logout
claude
```

## 2. Cấu hình toàn máy (global, cho Terminal)

Thêm vào `~/.zshrc`:

```bash
export ANTHROPIC_BASE_URL="https://openrouter.ai/api"
export ANTHROPIC_AUTH_TOKEN="sk-or-v1-..."
export ANTHROPIC_MODEL="anthropic/claude-sonnet-4"
```

```bash
source ~/.zshrc
```

## 3. Claude Desktop có bị ảnh hưởng không?

Không, nếu chỉ đặt biến trong `~/.zshrc`.

- Biến đó chỉ áp dụng cho process chạy từ shell (Claude Code trong Terminal).
- Claude Desktop chạy như app riêng, dùng đăng nhập riêng.
- Ngoại lệ duy nhất: mở Claude Desktop từ Terminal thì nó kế thừa biến môi trường.
  Đừng mở kiểu `open -a "Claude"` từ shell đang export key nếu muốn tách hẳn.
- Không dùng `launchctl setenv` để đặt key toàn hệ thống.

Muốn tách hẳn mà không cần alias, dùng cấu hình theo project ở mục 4.

## 4. Cấu hình theo từng project (khuyên dùng)

Tạo file riêng của project:

```bash
mkdir -p .claude
nano .claude/settings.local.json
```

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://openrouter.ai/api",
    "ANTHROPIC_AUTH_TOKEN": "sk-or-v1-...",
    "ANTHROPIC_MODEL": "anthropic/claude-sonnet-4"
  }
}
```

Chạy bình thường trong thư mục project:

```bash
claude
```

Quy ước file:

- `.claude/settings.json` — cấu hình chung, commit được, không chứa key.
- `.claude/settings.local.json` — cấu hình máy cá nhân / chứa key, thêm vào `.gitignore`.

```gitignore
.claude/settings.local.json
```

Cách khác nếu thích shell hơn JSON: dùng `direnv` với file `.envrc`,
rồi `direnv allow`. Kết quả tương đương: vào thư mục thì có biến,
ra khỏi thư mục thì biến tự gỡ.

## 5. Switch nhanh giữa nhiều provider

Tạo nhiều file:

```text
.claude/settings.openrouter.json
.claude/settings.anthropic.json
```

Ví dụ `.claude/settings.openrouter.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://openrouter.ai/api",
    "ANTHROPIC_AUTH_TOKEN": "sk-or-v1-...",
    "ANTHROPIC_MODEL": "anthropic/claude-sonnet-4"
  }
}
```

Chạy với file chỉ định:

```bash
claude --settings .claude/settings.openrouter.json
claude --settings .claude/settings.anthropic.json
```

Thêm vào `.gitignore` nếu file chứa key:

```gitignore
.claude/settings.*.json
```

Hoặc chỉ ignore file chứa key, giữ lại file mẫu không key để chia sẻ:

```text
.claude/settings.openrouter.example.json
```

## 6. Dùng trong VSCodium

Cấu hình trên là của **Claude Code**, áp dụng cả khi chạy từ VSCodium.

### 6.1. Terminal tích hợp trong VSCodium

Mở đúng thư mục project bằng **File → Open Folder**, mở terminal tích hợp, chạy:

```bash
claude
```

Claude Code tự đọc `.claude/settings.local.json` của project.

Switch nhanh:

```bash
claude --settings .claude/settings.openrouter.json
```

### 6.2. Extension Claude Code trong VSCodium

Nếu extension khởi động Claude Code trong workspace hiện tại,
`.claude/settings.local.json` vẫn được áp dụng. Lưu ý:

- Phải mở đúng thư mục project, không mở thư mục cha.
- Sau khi sửa file settings, khởi động lại session Claude Code.
- Extension có thể không có ô nhập `--settings`; khi đó dùng
  `settings.local.json` làm cấu hình đang hoạt động, hoặc chạy từ
  terminal tích hợp khi cần switch.
- VSCodium có thể phải cài extension Claude Code thủ công nếu không có trên Open VSX.

## 7. Bảo mật

- Không commit file chứa `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY`.
- Commit file mẫu không key (`.example.json`) để người khác tự điền.
- Đặt quyền hạn chế nếu lưu key trong file shell.
