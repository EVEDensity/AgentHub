# Release Unblocking Runbook (M3 / I-5)

> Status: active runbook
> Owner: release maintainers
> Date: 2026-09-01
> Scope: 从零把 desktop-v\* / cli-v\* 公开发布跑通的操作手册 —— 5 个签名
> secrets 的获取、配置与验证，以及两个 tag 发布流程。

## 现状与目标

代码侧发布链路已全部就绪：

- `desktop-v*` tag → `.github/workflows/desktop-windows.yml`：签名策略
  门禁（`release-policy.ps1 -PublicRelease`）→ 打包 + 三重 smoke →
  `publish-stack` job 构建完整 local-services 栈并附到 GitHub Release
  （manifest + 逐文件 sha256 + 资产名校验）。
- `cli-v*` tag → `.github/workflows/npm-cli.yml`：冻结 `agenthub.exe`
  → mock 通道闭环冒烟 → 发布 `@agenthub/cli` + `@agenthub/cli-win32-x64`。

唯一阻塞是 5 个签名 secrets 未配置（`release-policy.ps1` 会诚实拒绝）。
本手册把它们逐个流程化。

## Secrets 清单与获取方式

| Secret | 用途 | 获取方式 | 成本 |
|---|---|---|---|
| `AGENTHUB_WINDOWS_SIGNING_CERT_BASE64` | Windows 代码签名证书（PFX, base64） | 见 §A | 免费（自签/CI 证书）或付费（CA） |
| `AGENTHUB_WINDOWS_SIGNING_PASSWORD` | 上述 PFX 的密码 | 自定 | 免费 |
| `AGENTHUB_UPDATE_PRIVATE_KEY` | Tauri updater 签名私钥（minisign 风格） | `tauri signer generate` 本地生成 | 免费 |
| `AGENTHUB_UPDATE_PUBLIC_KEY` | updater 公钥（注入 Tauri updater 配置） | 与上同对 | 免费 |
| `AGENTHUB_UPDATE_ENDPOINT` | updater 清单 URL | 指向 Release 资产 | 免费 |

另有第 6 个可选 secret：

| Secret | 用途 |
|---|---|
| `AGENTHUB_UPDATE_PRIVATE_KEY_PASSWORD` | 私钥口令（生成时设了密码才需要） |

npm 发布线另需 `NPM_TOKEN`（npm automation token，对 `@agenthub` scope
有发布权）。PR 审查线另需 `AGENTHUB_REVIEW_MODEL_API_KEY`（模型通道 key）。

## §A 五步解阻塞（按顺序执行）

### 步骤 1：生成 updater 密钥对（本地，5 分钟）

```powershell
# 需已安装 Tauri CLI（cargo install tauri-cli --version 2.11.4 --locked）
cargo tauri signer generate -w "$env:USERPROFILE\.agenthub-update.key"
# 记录输出或文件内容：
#   私钥 → agenthub-update.key      → secret: AGENTHUB_UPDATE_PRIVATE_KEY
#   公钥 → agenthub-update.key.pub → secret: AGENTHUB_UPDATE_PUBLIC_KEY
# 生成时若设置口令 → secret: AGENTHUB_UPDATE_PRIVATE_KEY_PASSWORD
```

注意：密钥文件本身已在 `.gitignore`（`agenthub-up​date.key` /
`.key.pub`），绝不能提交。仓库根目录若残留旧文件，仅作参考，不入库。

### 步骤 2：确定 updater endpoint（1 分钟）

`AGENTHUB_UPDATE_ENDPOINT` 指向 updater 清单（`latest.json` /
`*-release.json`）的 URL。最简方案（零运维）：直接用 GitHub Release 资产
URL，首次发布后回填：

```
https://github.com/EVEDensity/AgentHub/releases/download/desktop-v<版本>/<文件>-release.json
```

发布顺序因此是：先配 §1 与 §A 步骤 3 的证书 secrets → 打第一个 tag →
Release 生成后拿到实际 asset URL → 回填 endpoint secret → 重打 tag（或下
一版本）。endpoint 不匹配只影响 updater 自动更新，不影响安装包本身。

### 步骤 3：Windows 代码签名证书（关键路径）

三条路线，按预算选择：

**路线 1（零成本，起步推荐）—— CI 临时自签证书：**

```powershell
# 本地生成自签代码签名证书并导出 PFX
$cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=AgentHub CI, O=EVEDensity" -CertStoreLocation Cert:\CurrentUser\My
$pfxBytes = $cert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Pfx, "<自定密码>")
[IO.File]::WriteAllBytes("$env:TEMP\agenthub-ci-codesign.pfx", $pfxBytes)
# base64 → secret: AGENTHUB_WINDOWS_SIGNING_CERT_BASE64
[Convert]::ToBase64String([IO.File]::ReadAllBytes("$env:TEMP\agenthub-ci-codesign.pfx")) | Set-Content "$env:TEMP\cert.b64"
```

注意（诚实声明）：自签证书不能消除 SmartScreen 警告——终端用户首次运行
仍会看到"未知发布者"。它解阻塞的是发布流水线（签名步骤不再缺证书），
不是信任链。对外推广前应升级到路线 2/3。

**路线 2（约 $70-200/年）—— OV 代码签名证书：**
Certum/Sectigo/GlobalSign 等购买 OV Code Signing（个人可用 Certum
Open Source，约 €69/年）。签发后导出 PFX → base64 → 同上配置。

**路线 3（约 $300+/年 + 硬件 token）—— EV 代码签名：**
即时 SmartScreen 信誉。机构级对外分发用。

### 步骤 4：配置 secrets（GitHub UI，5 分钟）

仓库 → Settings → Secrets and variables → Actions → New repository
secret，逐个添加：

```
AGENTHUB_WINDOWS_SIGNING_CERT_BASE64  = <cert.b64 文件全文>
AGENTHUB_WINDOWS_SIGNING_PASSWORD    = <PFX 密码>
AGENTHUB_UPDATE_PRIVATE_KEY          = <agenthub-update.key 内容>
AGENTHUB_UPDATE_PUBLIC_KEY           = <agenthub-update.key.pub 内容>
AGENTHUB_UPDATE_ENDPOINT             = <§步骤2 的 URL（可后回填）>
AGENTHUB_UPDATE_PRIVATE_KEY_PASSWORD = <私钥口令（若设置）>
```

npm 线（I-2 发布需要）：`NPM_TOKEN`。
PR 审查线（I-4 需要）：`AGENTHUB_REVIEW_MODEL_API_KEY`。

### 步骤 5：打 tag 发布

```powershell
# 桌面完整栈发布（触发 desktop-windows.yml 的全部链路）
git tag desktop-v0.3.0
git push origin desktop-v0.3.0

# CLI npm 发布（触发 npm-cli.yml）
git tag cli-v0.3.0
git push origin cli-v0.3.0
```

发布后在 Release 页把 draft 转正式（`publish-stack` 会自动创建 draft
Release 并附上完整栈资产）。

## 验收（north-star §5）

| 项 | 验证命令 |
|---|---|
| 一行安装 | 干净机器 `npm i -g @agenthub/cli && agenthub run "<目标>"` |
| 有公开分数 | `benchmarks/public-scores.md` 已有 2026-09-01 deepseek-v4-flash 8/8 |
| PR 审查可用 | 提交一个 PR，观察 review-pr.yml 运行与 findings summary |
| 桌面完整栈 | 新机器下载 Release 栈 → `agenthub upgrade <manifest-url>` 或首启向导 |

## 故障排查

- **release-policy 失败**：日志会列出缺失的 secret 名单，按 §步骤 4 补齐。
- **updater 签名失败**：确认 `TAURI_SIGNING_PRIVATE_KEY` 对应
  `AGENTHUB_UPDATE_PRIVATE_KEY`（workflow 已做映射），口令 secret 是否
  需要配。
- **npm publish 403**：`@agenthub` scope 未被账号拥有——先在 npmjs.com
  创建 org `agenthub`，或改用非 scope 名并同步改 `distributions/npm/`。
- **cli 冒烟失败**：看 frozen binary 的 `_serve` 日志（workspace 下
  `.agenthub/logs/`）。
