from django.core.management.base import BaseCommand

from catalog.models import Product, ProductCategory, ProductImage, ProductSpecification
from content.models import InteriorProject, InteriorProjectImage, PolicyPage, Publication
from core.models import SiteSettings
from inquiries.models import InquiryCategory
from orders.models import PaymentMethod
from content.policy_drafts import DRAFT_EFFECTIVE_DATE, DRAFT_VERSION, POLICY_DRAFTS


CATEGORIES = [
    ("daily-living", "居家生活", "HOME LIVING", "linen", ["陶器", "織品"], "從早晨的一杯水到深夜的一方織物，收錄讓日常慢慢安定下來的器物。"),
    ("incense-fragrance", "香道香氛", "INCENSE", "smoke", ["香器", "燭具"], "以一縷氣味整理房間，也替心緒留出一段安靜的空白。"),
    ("tea-selection", "茶道茶品", "TEA", "tea", ["茶器", "收納"], "從沏茶到入席，選擇經得起每日使用、也能與時間相處的茶器。"),
    ("art-collection", "藝術骨董", "ART & ANTIQUES", "ink", ["花器", "擺件"], "不急著填滿空間，只留下能被長久觀看、與生活互相照映的作品。"),
    ("healthy-living", "樂齡樂活", "SLOW AGING", "moss", ["餐具", "起居"], "讓身體自在、動作從容，以溫潤材質陪伴每一段生活節奏。"),
    ("pampered-pets", "寵愛寵物", "WITH PETS", "clay", ["食器", "休憩"], "為一同生活的夥伴選擇安全、耐用，也能安靜融入居家的日常用品。"),
]

PRODUCTS = [
    ("morning-cup", "RF-MC-001", "daily-living", "靜院晨物", "晨光器物", "素胎粗陶・晨飲杯", 1280, 12, False, "陶器", "clay", "限量 12 件", "質地粗糙而溫厚，握在掌心，如同一方安靜的小院。素燒未上釉，會隨日常使用留下柔和茶痕。", [("材質", "粗陶・素燒"), ("尺寸", "ø 8 × H 9 cm"), ("產地", "宜蘭・手作")]),
    ("wooden-spoon-set", "RF-WS-002", "healthy-living", "靜院器用", "日用木作", "原木長柄湯匙・三支組", 880, 20, False, "餐具", "wood", "", "細長的匙柄能輕鬆探入杯底與深碗，三支各有略微不同的木紋。表面僅以天然木蠟薄薄收整。", [("材質", "白蠟木・天然木蠟"), ("尺寸", "L 19 × W 2.8 cm／三支組"), ("產地", "台中・手作")]),
    ("linen-tablecloth", "RF-LT-003", "daily-living", "靜院布織", "薄霧織品", "亞麻粗織・桌巾（薄霧）", 2480, 8, False, "織品", "linen", "", "保留亞麻纖維自然的節點與皺褶，霧灰色在晨光與晚燈下各有不同表情。", [("材質", "100% 歐洲亞麻"), ("尺寸", "140 × 180 cm"), ("產地", "台南・織製")]),
    ("ceramic-vase", "RF-CV-004", "art-collection", "靜院選物", "靜物一器", "陶製花器・圓口", 3680, 1, False, "花器", "stone", "僅剩 1 件", "圓潤器身承接一兩枝葉，也能獨自成為桌上的靜物。礦物釉在窯火中留下不均勻的灰白層次。", [("材質", "陶土・礦物釉"), ("尺寸", "ø 15 × H 22 cm"), ("產地", "鶯歌・手作")]),
    ("woven-blanket", "RF-WB-005", "daily-living", "靜院居織", "霧雅織物", "手織棉毯・霧雅灰", 4200, 10, False, "織品", "fog", "", "輕柔但不單薄的棉毯，適合覆在沙發、床尾或夏夜膝上。", [("材質", "天然棉・手工織造"), ("尺寸", "130 × 180 cm"), ("產地", "彰化・織製")]),
    ("teak-coaster-set", "RF-TC-006", "tea-selection", "靜院茶具", "茶席木作", "柚木茶托・四件組", 1580, 16, False, "茶器", "tea", "", "微微下凹的圓面能穩穩承接杯底，也收住一滴茶水。四件木紋各異。", [("材質", "回收柚木・天然護木油"), ("尺寸", "ø 10 × H 1.2 cm／四件組"), ("產地", "嘉義・木作")]),
    ("brass-candle-holder", "RF-BC-007", "incense-fragrance", "靜院器物", "夜色微光", "黃銅燭台・低座", 2280, 9, False, "燭具", "brass", "", "低重心的黃銅底座托住一點燭光，不遮擋桌上視線。", [("材質", "實心黃銅"), ("尺寸", "ø 8 × H 4.5 cm"), ("產地", "台北・金工")]),
    ("bamboo-basket", "RF-BB-008", "tea-selection", "靜院居所", "竹影收納", "竹編提籃・方口", 1980, 8, False, "收納", "bamboo", "限量 8 件", "方正籃口便於收放茶布、茶罐與日常小物，提把保留移動時的輕巧。", [("材質", "桂竹・藤皮"), ("尺寸", "W 24 × D 18 × H 17 cm"), ("產地", "南投・竹編")]),
    ("stone-incense-holder", "RF-SI-009", "incense-fragrance", "靜院香席", "山霧香器", "手鑿石製香座・細孔", 1680, 15, False, "香器", "smoke", "", "一方掌心大小的天然石，中央細孔承接線香。刻意保留鑿痕與石材邊緣。", [("材質", "台灣蛇紋石"), ("尺寸", "約 W 7 × D 6 × H 2.5 cm"), ("產地", "花蓮・手作")]),
    ("aged-tea-jar", "RF-AT-010", "art-collection", "靜院茶藏", "日常茶藏", "錫釉茶罐・小藏", 2880, 2, False, "擺件", "ink", "僅剩 2 件", "低調的灰銀色茶罐，蓋口密合，適合保存少量常飲茶葉。", [("材質", "陶胎・錫色釉"), ("尺寸", "ø 9 × H 12 cm"), ("產地", "苗栗・手作")]),
    ("oak-reading-stool", "RF-OR-011", "healthy-living", "靜院起居", "從容日常", "白橡木閱讀矮凳", 5680, 0, True, "起居", "moss", "預購", "座面略微內收，起身時可自然扶握兩側。高度適合玄關穿鞋、床邊置物。", [("材質", "北美白橡木・植物油"), ("尺寸", "W 42 × D 28 × H 38 cm"), ("產地", "新竹・木作")]),
    ("pet-dining-bowl", "RF-PB-012", "pampered-pets", "靜院伴居", "一起生活", "霧白陶製寵物食器", 1480, 12, False, "食器", "clay", "", "寬口與低斜面讓貓犬更容易進食，厚實底部不易在地面滑動。", [("材質", "高溫陶・食品級釉"), ("尺寸", "ø 16 × H 6 cm"), ("產地", "鶯歌・製作")]),
    ("pet-rest-mat", "RF-PR-013", "pampered-pets", "靜院伴居", "一起生活", "棉麻編織寵物休憩墊", 2380, 10, False, "休憩", "linen", "", "棉麻表布帶著細緻織紋，薄墊能平放在窗邊、長椅或外出提籠中。", [("材質", "棉麻混紡・可拆式棉墊"), ("尺寸", "60 × 45 × H 3 cm"), ("產地", "台南・車縫")]),
]

PROJECTS = [
    ("bamboo-house-yilan", "宜蘭・竹屋", "Bamboo House, Yilan", "住宅空間", "宜蘭・員山", 2025, "48 坪", "Minimal Wabi", "bamboo", "我們把房子視為一只盛接光線的器皿。讓竹、灰泥與舊木安靜地留在空間裡。"),
    ("taipei-flat-renewal", "台北・老公寓改造", "Taipei Flat Renewal", "老屋改造", "台北・大同", 2024, "32 坪", "Modern Oriental", "fog", "在狹長的老公寓裡，我們用通透拉門與連續木作重新整理光線。"),
    ("hualien-seaside-stay", "花蓮・海邊民宿", "Hualien Seaside Stay", "民宿旅宿", "花蓮・壽豐", 2024, "76 坪", "Coastal Calm", "sea", "面向太平洋的旅宿不需要更多裝飾，讓風、潮聲與旅人的停留成為內容。"),
    ("tainan-tea-room", "台南・茶室", "Tainan Tea Room", "商業空間", "台南・中西區", 2025, "24 坪", "Quiet Contemporary", "tea", "一間街屋茶室，以低矮座席、溫潤土牆與內院微光放慢來客腳步。"),
]

PUBLICATIONS = [("no-04-qinghuan", "No. 04", "清歡", "一碗粥、一盞茶、一段晨光", 96, "rice"), ("no-03-anshen", "No. 03", "安身", "把家構成自己的道場", 88, "ink"), ("no-02-light-objects", "No. 02", "光與器", "器物在日光下的呼吸", 80, "sun"), ("no-01-courtyard", "No. 01", "院子", "從一扇窗開始", 72, "moss")]


class Command(BaseCommand):
    help = "將 Astro 示範網站的初始內容寫入資料庫（不會覆寫現有資料）。"

    def handle(self, *args, **options):
        SiteSettings.objects.get_or_create(pk=1)
        category_map = {}
        for index, (slug, name, english, tone, subcategories, description) in enumerate(CATEGORIES, 1):
            category, _ = ProductCategory.objects.get_or_create(slug=slug, defaults={"name": name, "english_name": english, "tone": tone, "subcategories": subcategories, "description": description, "sort_order": index, "is_active": True})
            category_map[slug] = category
        for index, row in enumerate(PRODUCTS, 1):
            slug, sku, category_slug, maker, series, name, price, stock, preorder, subcategory, tone, badge, description, specs = row
            product, created = Product.objects.get_or_create(slug=slug, defaults={"sku": sku, "category": category_map[category_slug], "maker": maker, "series": series, "name": name, "price": price, "stock": stock, "is_preorder": preorder, "preorder_note": "預購商品詳情將透過 LINE 說明。" if preorder else "", "subcategory": subcategory, "tone": tone, "badge_label": badge, "image_label": slug.replace("-", " ").upper(), "description": description, "short_description": description[:280], "care": "天然材質與手工作品各有細微差異。使用後請保持清潔與乾燥。", "shipping_note": "請查看正式退換貨政策。", "maker_story": f"由 {maker} 小量製作的生活器物。", "sort_order": index, "is_published": True})
            if created:
                for spec_order, (label, value) in enumerate(specs, 1):
                    ProductSpecification.objects.create(product=product, label=label, value=value, sort_order=spec_order)
                for image_order, suffix in enumerate(("主視圖", "細節圖", "使用情境"), 1):
                    ProductImage.objects.create(product=product, alt_text=f"{name}・{suffix}", sort_order=image_order, is_primary=image_order == 1)
        for index, row in enumerate(PROJECTS, 1):
            slug, title, english, project_type, location, year, area, style, tone, description = row
            project, created = InteriorProject.objects.get_or_create(slug=slug, defaults={"title": title, "english_title": english, "project_type": project_type, "location": location, "year": year, "area": area, "style": style, "description": description, "concept_title": "讓光線、材料與日常回到恰好的位置。", "design_notes": [description, "從採光、動線與每日使用開始整理，再讓材質與細節安靜並置。"], "materials": ["自然材質・手工收整", "礦物塗料・霧面牆體", "木作・收納與家具"], "image_label": english.upper(), "tone": tone, "published": True, "sort_order": index})
            if created:
                for image_order, label in enumerate(("MORNING LIGHT", "LIVING", "MATERIAL DETAIL", "EVENING"), 1):
                    InteriorProjectImage.objects.create(project=project, alt_text=f"{title}・{label}", caption=label, tone=tone, sort_order=image_order)
        for index, (slug, issue, title, subtitle, pages, tone) in enumerate(PUBLICATIONS, 1):
            Publication.objects.get_or_create(slug=slug, defaults={"issue_number": issue, "title": title, "subtitle": subtitle, "description": f"{subtitle}。空間、器物與生活的閱讀提案。", "page_count": pages, "tone": tone, "featured": index == 1, "published": True, "sort_order": index})
        recipients = [("design", "設計諮詢", "vicky725705@gmail.com"), ("media", "媒體合作", "vicky725705@gmail.com"), ("business", "商業合作", "vicky725705@gmail.com"), ("shopping", "購物 / 訂單", "rfullshop@gmail.com")]
        for index, (slug, name, email) in enumerate(recipients, 1):
            InquiryCategory.objects.get_or_create(slug=slug, defaults={"display_name": name, "recipient_email": email, "active": True, "sort_order": index})
        method_names = {"taiwan_pay": "台灣 Pay", "credit_card": "信用卡", "bank_transfer": "銀行轉帳", "paypal": "PayPal"}
        for index, (code, name) in enumerate(method_names.items(), 1):
            PaymentMethod.objects.get_or_create(code=code, defaults={"display_name": name, "enabled": False, "sort_order": index})
        for index, (slug, (title, body)) in enumerate(POLICY_DRAFTS.items(), 1):
            page, created = PolicyPage.objects.get_or_create(slug=slug, defaults={"title": title, "body": body, "version": DRAFT_VERSION, "effective_date": DRAFT_EFFECTIVE_DATE, "published": True, "legal_reviewed": False, "sort_order": index})
            if not created and not page.body.strip():
                page.title, page.body, page.version, page.effective_date, page.published = title, body, DRAFT_VERSION, DRAFT_EFFECTIVE_DATE, True
                page.save()
        self.stdout.write(self.style.SUCCESS("已檢查並建立初始資料。"))
