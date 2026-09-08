# Windows → GitHub Actions → iPhone／iPad

## 四個步驟的修正

| 步驟 | 實際做法 |
|---|---|
| 開發 | Windows Godot 4.5.1 標準版，GDScript |
| 雲端打包 | GitHub Actions 的 macOS + Xcode，產生未簽署 IPA |
| 側載 | Windows Sideloadly 或 AltStore Classic，使用自己的 Apple ID 完成簽署安裝；兩者都是第三方工具，不是 Apple 官方工具 |
| 區網即時測試 | Xogot Connect；裝置端需要付費 Xogot 使用權，且需要配對與版本相容驗收 |

如果「Godot Remote」指 DmitriySalnikov/GodotRemote，那是 Godot 3 的模組，專案已於 2024 年封存，不適合作為這個 Godot 4 iOS 專案的預設方案。[原專案](https://github.com/DmitriySalnikov/GodotRemote)

## 第一次建立 GitHub 儲存庫

1. 使用 GitHub Desktop，把解壓後**直接含有 `project.godot` 的資料夾**建立為本機 repository。
2. 第一次 Commit 後 Publish repository；個人開發可先選 Private。根目錄應直接有 `.github/workflows/`，不要再包一層資料夾。
3. 確認 `.godot/`、簽署憑證與 `export_credentials.cfg` 沒有被加入；專案已提供 `.gitignore`。
4. 在 `export_presets.cfg` 把 `com.example.pytorpg` 改成你自己固定的識別碼，例如 `com.yourname.pytorpg`。這個值之後盡量不變。
5. 推送後，在 GitHub Actions 看 `Check Godot project` 是否成功。

目前没有替你建立／上傳 GitHub 儲存庫，也沒有雲端執行結果。整個專案可直接作為 repository 內容；不需要把原始 100 MB 附件 ZIP 再提交一次。

## 產生與安裝獨立 App

1. GitHub → Actions → **Build iOS unsigned IPA** → **Run workflow**。
2. 成功後在該次執行下方 Artifacts 下載 `PytoRPG-iOS-unsigned-數字`。
3. 解壓 artifact ZIP，取得 `PytoRPG-unsigned.ipa`。
4. 依 [Sideloadly 官方網站](https://sideloadly.io/) 安裝 Windows 版及其要求的 Apple 驅動／元件。
5. 首次以 USB 連接、解鎖 iPhone／iPad並信任電腦；把 IPA 拖進 Sideloadly，選裝置與自己的 Apple ID，執行安裝。若裝置要求開發者模式或信任開發者，依装置提示完成。
6. 安裝成功後，從主畫面的 App 圖示開啟。第一次需驗收觸控、存檔、進背景／返回與兩種裝置畫面。

此流程把 Apple ID 的簽署留在本機，不需要把 Apple ID 密碼填入 GitHub Secrets。`UNSIGNED00` 是供 Godot 產生 Xcode 專案的占位 Team ID，**不是可用的 Apple Team ID**；Xcode 建置步驟關閉簽署，最後由側載工具重新簽署。未簽署 IPA 無法直接點一下在 iOS 安裝，也不能直接上傳 TestFlight。

Godot 版本与模板由工具固定，下載後對照官方 SHA-512。macOS runner 的 Xcode 仍可能隨 runner 映像更新；工作流程會記錄版本，首次雲端成功後可再固定 Xcode。若建置失敗，要依該次 log 修正，不能把目前設定當作已通過實際打包。

## 日常測試：已附 Xogot Connect 外掛

1. Windows Godot：Project → Project Settings → Plugins → 啟用 **Xogot Connect**。
2. 手機／平板安裝 Xogot，取得裝置端付費使用權，打開 Remote 頁面並允許區域網路存取。
3. Windows 與裝置連同一區網；允許 Godot 通過私人網路防火牆。
4. 在 Godot 的 Xogot 面板搜尋裝置並按照配對提示操作。若顯示登入流程，使用與裝置相同的 Xogot 帳號。各版介面可能不同，依 [Xogot Connect 文件](https://docs.xogot.com/documentation/xogot/xogot-connect/) 為準。
5. Remote Deploy 啟動。可做中斷點、場景樹與屬性即時調整；大幅程式變更通常重新部署。

此外掛的原始碼來源與 commit 記在 `xogot-version.txt`，授權在 `Xogot-LICENSE.txt`。外掛預設未啟用，避免在尚未配對時改動編輯器設定。它是開發工具，獨立 IPA 匯出排除 `addons/`。

本版已有遊戲自己的觸控控制，不必啟用 Xogot 額外虛擬控制器。桌面引擎與裝置端 Xogot 的 Godot 版本要能相容；「支援 Godot 4.4.1 以上」不代表所有未來引擎／外掛組合都已在此專案實測。[Xogot 官方功能與付費條件](https://xogot.com/connect/)、[外掛來源](https://github.com/xibbon/xogot_connect)

## 如何更新比較方便

| 變更 | 操作 |
|---|---|
| 開發中的腳本、圖片、地圖 | 本機 F5，再用 Xogot Remote Deploy；不用每次編譯 IPA |
| 要帶出門獨立遊玩 | GitHub Desktop Commit + Push，再按 Run workflow，下載新 IPA 覆蓋安裝 |
| 同時發 Windows 與 iOS 版本 | 建立 `v0.1.1` 之類的版本標籤並推送；兩個 build workflow 都會觸發 |
| 免費簽署即將到期 | 讓側載工具定期重新簽署；這只是續期，不會自動下載 GitHub 的新版本 |

免費 Apple ID 的側載有效期通常是 7 天，Sideloadly 提供自動續簽，但仍有電腦、網路與帳號條件。不要把它理解成永久免維護安裝。[Sideloadly 說明](https://sideloadly.io/)

本版没有啟用 App 內下載新程式／自動套用 PCK 的熱更新。初期採用 Xogot 快速測試與完整 IPA 更新，容易重現版本與回退。若日後要內容更新，可增加僅含 JSON／圖片的資料包、版本與雜湊驗證、下載暫存、啟動切換及失敗回復；不應在尚未完成存檔遷移時直接覆寫世界資料。

## 存檔與更新

- 0.2.0 新進度位於 `user://progress_v2.json`，備份為 `.bak`；第一階段 `save_v1.json` 只在沒有新進度時讀取，不覆寫。
- iOS 使用 App 沙盒。同一簽署帳號與相同 Bundle ID 覆蓋安裝通常可保留；刪除 App、改識別碼或側載工具重建安裝可能失去原資料。
- iPhone 與 iPad 各自存檔；目前沒有 iCloud 同步。Xogot 執行的存檔與獨立 App 也不是同一個沙盒。
- 0.2.0 保存位置、生命、背包、生物、掉落、寶箱與各世界狀態；學習另存 `study_v1.json`，重開仍繼續待答題。舊 Pyto v19 存檔仍需另外對映，待有實際存檔樣本再驗收。
- 若要長期提供他人更方便更新，可後續加入 Apple Developer Program 的正式簽署與 TestFlight 上傳流程；這一包尚未實作。TestFlight 每個 build 最多測試 90 天。[Apple 文件](https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview)

macOS Actions 用量與費用依 repository 與帳號方案決定，不保證免費。這份配置在平常 push 跑 Linux 檢查，只有手動／標籤發版才用 macOS。[GitHub 計費說明](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
