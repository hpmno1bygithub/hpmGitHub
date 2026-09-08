# Phase 6：冰層、熱特效、踏階與 iOS 操作

本版延續 Phase 5，可用 Windows Godot 4.5／4.5.1 開啟 `project.godot`。原版檔案保留於 reference，原有九張地圖不改動；新增的 thermal_lab 僅供驗收。

## 本次修正

- 冰改為獨立碰撞與繪製層，同一格可以同時有底下的土石與上方冰層。底面貼齊 1/3、2/3 地形，不再整格往上推。薄／中／厚冰採原版 ice_layers.py 與 config.py 的質量分界和高度 0.28／0.58／1.00。
- 火球依碰撞法線向圖塊內側取樣，四面命中均能融冰。遵循原版 FIX131：同一發火球只完成 ICE → WATER，後一發才處理 WATER → STEAM；三級火球直接命中冰也不以碎片二次蒸發融水。
- 溫帶、沙漠、雨林採原版溫度與融冰倍率；雪山／極地低溫冰須受火或岩漿等明確熱源才融化。地圖編輯器放置的普通冰圖塊也能受熱融化。
- 蒸發、火球汽化、水與岩漿接觸會記錄水汽量、上升氣流；畫面使用原版方塊風格的蒸氣、薄霧、煙與火星，隨時間向上、擴散、淡出，遇屋頂不穿過岩石。特效最多 256 顆，氣流衰減，避免手機長時間堆積。
- 自動踏階以目前姿勢碰撞框做向上／橫向／向下掃描；站立、蹲走、左右行走可跨 1/3 格，2/3 格以上仍須跳，天花板不足時不強行擠上。
- 蹲走維持蹲姿，移動搖桿向上站起、向下與蹲鍵採單次切換；右搖桿保留上次準心，可拖曳瞄準後放開射擊／施法。
- 遊戲主操作區依原版 FIX131 `touch_controls_uicontrol.py:layout`、`control_overlay.py` 的座標公式：左移動搖桿，右跳躍／動作／蹲／翻滾／互動，工具／魔法／動作路徑選擇器，上方背包／新遊戲／儲存／載入／選單。使用既有原版 control_icons。iPhone 與 iPad 各用原版尺寸，額外套安全區。
- 桌面鍵盤操作保留；舊工具列由選單的「開發工具列」展開，避免擋住手機畫面。
- 修正截圖中的型別不一致、未使用變數、同名遮蔽、整數除法與單獨三元運算警告，未停用警告檢查。

## 怎麼驗收

開遊戲 →「新開遊戲」或「載入遊戲」。進入後點右上叉圖示開選單 →「開發工具列」→「探索地點」→「熱流體／踏階試驗場」。右方依序有單層階、兩層障礙、分層冰池、可燃樹木，以及水／岩漿。新試驗場冰池位於雪山氣候，便於測試火球；在一般暖區用冰球製冰則會自然融化。

鍵盤：A/D 移動，Space 跳，Shift 滾，C 蹲／趴，Z 趴，↑ 站起／爬，↓ 下梯／平台；J 動作，1 武器、2 魔法、3 工具，R 換魔法、T 換工具、V 換武器，滑鼠或 U/I/O/K 瞄準，F 採集、Q 飛鉤。觸控工具圖示循環鐵鎬／斧頭／鉤爪，魔法圖示循環火／水／冰／電，下方路徑圖示切換武器／魔法／工具。

## 驗證與保留範圍

`tools/check_project.py` 驗證場景匯入與 smoke／gameplay／phase3／phase4／phase5／phase6，檢查退出碼、完成標記與所有腳本警告。Phase6 涵蓋冰底部接縫、四向實際射擊、融冰守恆、暖／冷氣候、岩漿熱源、特效上升與回收、站／蹲左右踏階、低天花板、多指操作、手機／平板觸控區互斥。Windows 系統憑證存放區讀取訊息是測試主機的引擎初始化訊息，與腳本無關，驗證僅排除此一已知訊息。

本次是指定問題的修復，並非宣稱 87,138 行 Python 原作已全部移植。完整的氣候／地熱擴散／土壤水文／雲雨與生態循環、部分特殊武器及編輯器進階工具，仍以 Phase5 驗收文件列出的缺項為準。此版氣流用於環境資料與粒子運動，尚未把原版完整 WindSystem 的所有生物／翼裝力學接入；HUD 尚未補齊原版耐力與完整小地圖系統。iOS 實機的效能、安全區与多指手感仍需在安裝後驗收。

## iPhone / iPad 打包及更新

使用帳號指定的 `hpmno1bygithub/hpmGitHub` 儲存庫。`Build iOS unsigned IPA` 在 macOS 15 runner 安裝並驗證 Godot 4.5.1 官方下載的 SHA-512，執行上述測試，匯出 Xcode 專案，編譯真實 arm64 app，再建立 `Payload/PytoRPG.app` 結構的 `PytoRPG-unsigned.ipa`。產物另含 SHA-256 與 build-info.json；流程不假造或重新命名原始碼 ZIP 為 IPA。

首次可透過 GitHub 網頁上傳 `PytoRPG-source.zip`（內容根目錄 PytoRPG_Godot），再建立 `.github/workflows/build-ios.yml` 並執行。初始工作會展開完整原始碼到儲存庫，保留已存在的 workflow，以 github-actions bot 提交，往後可直接用 Git 在 Windows 開發與更新，不必一直傳 ZIP。

後續：更新原始碼並 push → GitHub Actions → Build iOS unsigned IPA → Run workflow → 等待成功 → 下載 `PytoRPG-iOS-unsigned-編號` artifact → 解壓取得 IPA。在 Windows 使用 AltStore Classic／AltServer 或 Sideloadly 以自己的 Apple ID 簽章並側載。未簽章 IPA 不是可在 iPhone「檔案」中點開直接安裝的檔案。更新時保持 bundle identifier 與簽章帳號一致，覆蓋安裝前備份遊戲存檔與內容包，避免刪 App 導致資料遺失。

Godot 官方說明 iOS 匯出需要 macOS 與 Xcode：https://docs.godotengine.org/en/stable/tutorials/export/exporting_for_ios.html
AltStore 官方 Windows 安裝指引：https://faq.altstore.io/altstore-classic/how-to-install-altstore-windows

檔案：`scripts/environment.gd`、`liquids.gd` 管冰與熱；`player.gd` 管踏階；`mobile_hud.gd`、`aim_pad.gd` 管觸控；`data/thermal_rules.json` 為原版常數；`tools/build_ios.py` 負責 IPA。要增添 Godot 場景與資源可以沿用這些邊界，不必繼續往 main.gd 堆所有功能。
