import streamlit as st
from streamlit_geolocation import streamlit_geolocation
import folium
from folium.plugins import LocateControl
from streamlit_folium import st_folium
import json
import os
from datetime import datetime
import base64
from PIL import Image
import io
import hashlib
from geopy.distance import great_circle
import time
import googlemaps
import openai
import re

# --- 設定と初期化 ---
DATA_DIR = "data"
USERS_FILE = os.path.join(DATA_DIR, "users.json")
NOTES_FILE = os.path.join(DATA_DIR, "notes.json")

os.makedirs(DATA_DIR, exist_ok=True)

# --- データ管理 & ヘルパー関数 ---
def load_data(file_path, is_dict=False):
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            if content:
                return json.loads(content)
    return {} if is_dict else []

def save_data(file_path, data):
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def image_to_base64(image, format="PNG"):
    buffered = io.BytesIO()
    if image.mode in ("RGBA", "P"):
        image = image.convert("RGB")
    image.save(buffered, format=format)
    return "data:image/png;base64," + base64.b64encode(buffered.getvalue()).decode()

def parse_hashtags(tag_string):
    if not tag_string:
        return []
    tags = {f"#{tag.lstrip('#')}" for tag in tag_string.split() if tag}
    return sorted(list(tags))

# --- Google Maps API関連の関数 ---
def get_gmaps_client():
    try:
        api_key = st.secrets["Maps_api_key"]
        return googlemaps.Client(key=api_key)
    except Exception:
        st.error("Google Maps APIキーがst.secretsに設定されていません。")
        return None

# --- OpenAI API関連の関数 ---
def get_openai_client():
    try:
        client = openai.OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
        return client
    except Exception:
        st.error("OpenAI APIキーがst.secretsに設定されていません。")
        return None

def generate_initial_notes(gmaps, lat, lng):
    st.info("現在地情報を基に、周辺の初期ノートを生成しています...少々お待ちください。")
    try:
        search_location = (lat, lng)
        place_types = ['cafe', 'park', 'tourist_attraction', 'restaurant', 'art_gallery']
        initial_notes = []
        processed_place_ids = set()

        for place_type in place_types:
            response = gmaps.places_nearby(location=search_location, radius=1500, language='ja', type=place_type)
            for place in response.get('results', []):
                place_id = place.get('place_id')
                if place_id and place_id not in processed_place_ids:
                    loc = place['geometry']['location']
                    new_note = {
                        "id": place_id, "title": place.get('name', '名称不明'),
                        "hashtags": [f"#{tag}" for tag in place.get('types', [])],
                        "lat": loc['lat'], "lng": loc['lng'],
                        "creator_id": "system", "creator_name": "自動生成",
                        "entries": [{"author_name": "システム", "timestamp": datetime.now().timestamp(), "type": "text",
                                     "data": f"これは{place.get('name', '')}の思い出ノートです。", "hashtags": [f"#{place_type}"]}]
                    }
                    initial_notes.append(new_note)
                    processed_place_ids.add(place_id)

        if initial_notes:
            save_data(NOTES_FILE, initial_notes)
            st.success(f"あなたの現在地周辺に {len(initial_notes)}件の初期ノートを生成しました。")
            time.sleep(3)
        else:
            st.warning("周辺にノートを生成できる適切な場所が見つかりませんでした。")
    except Exception as e:
        st.error(f"Google Maps APIとの通信中にエラーが発生しました: {e}")

# --- セッションステートの初期化 ---
for key, default in {
    "current_user": None, "center": None, "zoom": 15, "mode": "ノート設置モード",
    "nearby_notes": [], "selected_note_id": None, "user_location": None,
    "notified_notes": set(), "initial_load": False, "auto_refresh": True,
    "search_results": None, "initial_notes_generated": False,
    "clicked_location": None,
    "main_menu": "📖 ノート操作",
    "chat_messages": [],
    "recommended_note_id": None,
    "chat_started": False
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# --- 認証ページ ---
if not st.session_state.current_user:
    st.header("思い出ノートへようこそ 📖")
    users_data = load_data(USERS_FILE, is_dict=True)

    login_tab, register_tab = st.tabs(["ログイン", "新規登録"])
    with login_tab:
        with st.form("login_form"):
            login_id = st.text_input("ユーザーID")
            login_password = st.text_input("パスワード", type="password")
            submitted = st.form_submit_button("ログイン")
            if submitted:
                user = users_data.get(login_id)
                if user and user["password_hash"] == hash_password(login_password):
                    st.session_state.current_user = user
                    st.session_state.initial_load = True
                    st.rerun()
                else:
                    st.error("ユーザーIDまたはパスワードが正しくありません。")
    with register_tab:
        with st.form("register_form"):
            register_id = st.text_input("ユーザーID")
            register_name = st.text_input("ユーザー名")
            register_password = st.text_input("パスワード", type="password")
            submitted = st.form_submit_button("登録する")
            if submitted:
                if not (register_id and register_name and register_password):
                    st.warning("すべての項目を入力してください。")
                elif register_id in users_data:
                    st.error("そのユーザーIDは既に使用されています。")
                else:
                    new_user = {
                        "id": register_id, "name": register_name,
                        "password_hash": hash_password(register_password)
                    }
                    users_data[register_id] = new_user
                    save_data(USERS_FILE, users_data)
                    st.session_state.current_user = new_user
                    st.session_state.initial_load = True
                    st.success(f"{register_name} さん、登録が完了しました！")
                    st.rerun()

# --- メインアプリケーション ---
else:
    # --- 👇 修正箇所 1: AIによるモード切替フラグの処理 ---
    # st.rerunの後にこのブロックが実行され、安全にsession_stateを変更する
    if st.session_state.get("_switch_to_note_mode"):
        st.session_state.main_menu = "📖 ノート操作"
        st.session_state.mode = "ノート書き込みモード"
        del st.session_state["_switch_to_note_mode"] # フラグを削除して無限ループを防ぐ
    # --- 👆 修正完了 ---

    gmaps = get_gmaps_client()
    openai_client = get_openai_client()
    all_notes = load_data(NOTES_FILE)
    current_user_info = st.session_state.current_user
    st.set_page_config(layout="wide")

    location = streamlit_geolocation()
    if location and location.get('latitude'):
        st.session_state.user_location = location

    if gmaps and not all_notes and st.session_state.user_location and not st.session_state.initial_notes_generated:
        generate_initial_notes(gmaps, st.session_state.user_location['latitude'], st.session_state.user_location['longitude'])
        st.session_state.initial_notes_generated = True
        st.rerun()

    if st.session_state.search_results is not None:
        notes_to_display = st.session_state.search_results
    else:
        notes_to_display = all_notes

    if st.session_state.get('initial_load', False) and st.session_state.user_location:
        st.session_state.center = [st.session_state.user_location['latitude'], st.session_state.user_location['longitude']]
        st.session_state.zoom = 15
        st.session_state.initial_load = False
        st.rerun()

    if st.session_state.center is None:
        st.info("📍 現在位置を取得しています...（ブラウザの許可が必要です）")
    else:
        if st.session_state.user_location and not st.session_state.search_results:
            user_coords = (st.session_state.user_location['latitude'], st.session_state.user_location['longitude'])
            st.session_state.nearby_notes = [note for note in all_notes if great_circle(user_coords, (note['lat'], note['lng'])).km <= 10]

        with st.sidebar:
            st.header(f"ようこそ、{current_user_info['name']}さん")
            selected_menu = st.radio("メインメニュー", ("📖 ノート操作", "🔍 検索", "⚙️ アカウント"), horizontal=True, label_visibility="collapsed", key="main_menu")
            st.markdown("---")
            placeholder = st.empty()

            with placeholder.container():
                if selected_menu == "📖 ノート操作":
                    st.subheader("モードを選択")
                    mode = st.radio("実行したい操作", ["ノート設置モード", "ノート書き込みモード"], key="mode", label_visibility="collapsed")
                    st.markdown("---")

                    if mode == "ノート設置モード":
                        st.subheader("📖 ノートを設置する")
                        if not st.session_state.get('clicked_location'):
                            st.info("地図上をクリックして、ノートの設置場所を決めてください。")
                        else:
                            clicked_lat = st.session_state.clicked_location['lat']
                            clicked_lng = st.session_state.clicked_location['lng']
                            with st.form("popup_note_form", clear_on_submit=True):
                                st.write(f"設置座標: `{clicked_lat:.4f}, {clicked_lng:.4f}`")
                                note_title = st.text_input("ノートのタイトル")
                                note_hashtags = st.text_input("ハッシュタグ（スペース区切り）", placeholder="例:ランチ 絶景")
                                submitted = st.form_submit_button("この場所にノートを設置 ✨")
                                if submitted:
                                    if note_title:
                                        new_note = {
                                            "id": str(datetime.now().timestamp()), "title": note_title,
                                            "hashtags": parse_hashtags(note_hashtags), "lat": clicked_lat, "lng": clicked_lng,
                                            "creator_id": current_user_info['id'], "creator_name": current_user_info['name'],
                                            "entries": []
                                        }
                                        all_notes.append(new_note)
                                        save_data(NOTES_FILE, all_notes)
                                        st.success(f"ノート「{note_title}」を設置しました！")
                                        st.balloons()
                                        st.session_state.clicked_location = None
                                        st.rerun()
                                    else:
                                        st.warning("ノートのタイトルを入力してください。")
                            if st.button("キャンセル"):
                                st.session_state.clicked_location = None
                                st.rerun()

                    elif mode == "ノート書き込みモード":
                        st.subheader("✍️ ノートに書き込む")
                        notes_for_selection = notes_to_display

                        if not notes_for_selection:
                            st.info("表示できるノートがありません。")
                        else:
                            note_ids = [note['id'] for note in notes_for_selection]
                            index = None
                            if st.session_state.selected_note_id and st.session_state.selected_note_id in note_ids:
                                index = note_ids.index(st.session_state.selected_note_id)

                            note_options = {note['id']: f"📖 {note['title']} ({note['creator_name']})" for note in notes_for_selection}
                            selected_id = st.selectbox("書き込むノートを選択", options=list(note_options.keys()), format_func=lambda x: note_options[x], index=index, placeholder="ノートを選んでください")

                            if selected_id and selected_id != st.session_state.selected_note_id:
                                st.session_state.selected_note_id = selected_id
                                st.session_state.recommended_note_id = None
                                selected_note_obj = next((n for n in all_notes if n['id'] == selected_id), None)
                                if selected_note_obj:
                                    st.session_state.center = [selected_note_obj['lat'], selected_note_obj['lng']]
                                    st.session_state.zoom = 17
                                st.rerun()

                elif selected_menu == "🔍 検索":
                    st.subheader("場所で検索")
                    search_query = st.text_input("地名や住所で検索...", key="main_search")
                    if st.button("検索", key="main_search_btn"):
                        if gmaps and search_query:
                            geocode_result = gmaps.geocode(search_query, language='ja')
                            if geocode_result:
                                loc = geocode_result[0]['geometry']['location']
                                st.session_state.center = [loc['lat'], loc['lng']]
                                st.session_state.zoom = 17
                                st.success(f"「{geocode_result[0]['formatted_address']}」に移動しました。")
                                st.rerun()
                            else:
                                st.warning("場所が見つかりませんでした。")

                    st.markdown("---")
                    st.subheader("ハッシュタグで検索")
                    hashtag_query_input = st.text_input("検索タグ（スペース区切り）", placeholder="例: ランチ 楽しい")
                    search_mode = st.radio("検索モード", ["AND (すべて含む)", "OR (いずれかを含む)"], key="search_mode")
                    if st.button("検索する", key="search_hashtag_btn"):
                        if hashtag_query_input:
                            queries = [f"#{q.lstrip('#')}" for q in hashtag_query_input.split()]
                            found_notes = []
                            for note in all_notes:
                                all_tags_in_note = set(note.get("hashtags", []))
                                for entry in note.get("entries", []):
                                    all_tags_in_note.update(entry.get("hashtags", []))
                                if "AND" in search_mode:
                                    if all(q in all_tags_in_note for q in queries): found_notes.append(note)
                                else:
                                    if any(q in all_tags_in_note for q in queries): found_notes.append(note)
                            st.session_state.search_results = found_notes
                            st.success(f"「{' '.join(queries)}」で{len(found_notes)}件のノートが見つかりました。")
                            st.rerun()
                        else:
                            st.warning("検索するハッシュタグを入力してください。")

                    if st.session_state.search_results is not None:
                        if st.button("検索をクリア"):
                            st.session_state.search_results = None
                            st.rerun()

                    st.markdown("---")
                    st.subheader("🤖 AIにおすすめを聞く")

                    if not all_notes:
                        st.warning("まだノートがありません。AIに相談する前にノートを作成してください。")
                    elif openai_client:
                        if st.button("AIと相談を始める", key="start_chat_btn"):
                            st.session_state.chat_started = True
                            st.session_state.chat_messages = [
                                {"role": "assistant", "content": "こんにちは！どんな場所の思い出に浸りたい気分ですか？例えば、「静かな場所」「美味しいものが食べられる場所」など、あなたの気分や興味を教えてください。"}
                            ]
                            st.session_state.recommended_note_id = None
                            st.rerun()

                        if st.session_state.chat_started:
                            for msg in st.session_state.chat_messages:
                                st.chat_message(msg["role"]).write(msg["content"])

                            if prompt := st.chat_input("気分や要望をどうぞ"):
                                st.session_state.chat_messages.append({"role": "user", "content": prompt})
                                st.chat_message("user").write(prompt)

                                with st.spinner("AIが考えています..."):
                                    notes_summary_list = []
                                    for note in all_notes:
                                        summary = {
                                            "id": note["id"],
                                            "title": note["title"],
                                            "hashtags": note.get("hashtags", []),
                                            "first_entry": note["entries"][0]["data"] if note.get("entries") else "書き込みなし"
                                        }
                                        notes_summary_list.append(summary)
                                    notes_json_str = json.dumps(notes_summary_list, ensure_ascii=False)

                                    system_prompt = f"""
                                    あなたは、ユーザーとの対話を通じて、最適な「思い出ノート」を提案するAIアシスタントです。
                                    提供されたノートリストの中から、ユーザーの気分や要望に最も合うものを1つだけ選んでください。
                                    会話の最後には、必ず提案するノートのIDを `{{ "recommended_note_id": "(ここにID)" }}` というJSON形式のみで回答してください。
                                    JSONの前後に他のテキストは含めないでください。

                                    利用可能なノートリスト:
                                    {notes_json_str}
                                    """

                                    messages_for_api = [
                                        {"role": "system", "content": system_prompt}
                                    ] + st.session_state.chat_messages

                                    try:
                                        response = openai_client.chat.completions.create(
                                            model="gpt-4-turbo",
                                            messages=messages_for_api,
                                            temperature=0.7,
                                        )
                                        msg = response.choices[0].message.content
                                        json_match = re.search(r'\{\s*"recommended_note_id":\s*".*?"\s*\}', msg, re.DOTALL)

                                        if json_match:
                                            json_str = json_match.group(0)
                                            conversation_text = msg.replace(json_str, "").strip()

                                            try:
                                                reco_data = json.loads(json_str)
                                                reco_id = reco_data.get("recommended_note_id")

                                                if reco_id:
                                                    if conversation_text:
                                                        st.session_state.chat_messages.append({"role": "assistant", "content": conversation_text})
                                                        st.chat_message("assistant").write(conversation_text)
                                                        time.sleep(2)

                                                    # --- 👇 修正箇所 2: AI提案後の処理 ---
                                                    # session_stateを直接変更せず、一時フラグを立ててrerunする
                                                    st.session_state.recommended_note_id = reco_id
                                                    st.session_state.selected_note_id = reco_id
                                                    st.session_state.chat_started = False
                                                    st.session_state._switch_to_note_mode = True # 👈 一時フラグを設定

                                                    recommended_note = next((n for n in all_notes if n['id'] == reco_id), None)
                                                    if recommended_note:
                                                        st.success(f"AIがあなたに「{recommended_note['title']}」をおすすめしました！")
                                                        st.session_state.center = [recommended_note['lat'], recommended_note['lng']]
                                                        st.session_state.zoom = 17

                                                    st.rerun() # 👈 アプリケーションを再実行
                                                    # --- 👆 修正完了 ---

                                            except json.JSONDecodeError:
                                                st.session_state.chat_messages.append({"role": "assistant", "content": msg})
                                                st.rerun()
                                        else:
                                            st.session_state.chat_messages.append({"role": "assistant", "content": msg})
                                            st.rerun()

                                    except Exception as e:
                                        st.error(f"AIとの通信中にエラーが発生しました: {e}")

                        if st.session_state.chat_started and st.button("相談をやめる"):
                            st.session_state.chat_started = False
                            st.session_state.chat_messages = []
                            st.rerun()

                elif selected_menu == "⚙️ アカウント":
                    st.subheader("アプリ設定")
                    st.toggle("🗺️ マップの自動更新", key="auto_refresh", help="ONにすると5秒ごとに現在地と近くのノートを自動で更新します。")
                    st.markdown("---")
                    st.subheader("アカウント操作")
                    if st.button("ログアウト"):
                        for key in st.session_state.keys():
                            del st.session_state[key]
                        st.rerun()

        col1, col2 = st.columns([2, 1])

        with col1:
            m = folium.Map(
                location=st.session_state.center,
                zoom_start=st.session_state.zoom
            )

            LocateControl(auto_start=False, position='topright').add_to(m)

            if st.session_state.user_location:
                folium.CircleMarker(
                    location=[st.session_state.user_location['latitude'], st.session_state.user_location['longitude']],
                    radius=10, color='blue', fill=True, fill_color='blue', fill_opacity=0.6, popup='あなたの現在地'
                ).add_to(m)

            for note in notes_to_display:
                is_recommended = (note['id'] == st.session_state.get('recommended_note_id'))
                icon_color = 'purple' if is_recommended else 'beige'
                popup_text = f"👑 AIのおすすめ！\n" if is_recommended else ""
                popup_text += f"📖 {note['title']}\n設置者: {note['creator_name']}"

                folium.Marker(
                    location=[note['lat'], note['lng']],
                    popup=popup_text,
                    icon=folium.Icon(color=icon_color, icon='book', prefix='fa')
                ).add_to(m)

            map_data = st_folium(m, width="100%", height=550, center=st.session_state.center, zoom=st.session_state.zoom)

            if map_data and map_data.get("last_clicked") and st.session_state.mode == "ノート設置モード":
                if st.session_state.get('clicked_location') != map_data["last_clicked"]:
                    st.session_state.clicked_location = map_data["last_clicked"]
                    st.rerun()

            if map_data and "center" in map_data and map_data["center"] is not None:
                if st.session_state.center != [map_data["center"]["lat"], map_data["center"]["lng"]]:
                    st.session_state.center = [map_data["center"]["lat"], map_data["center"]["lng"]]
                if st.session_state.zoom != map_data["zoom"]:
                    st.session_state.zoom = map_data["zoom"]

        with col2:
            is_viewable = False
            selected_note = None

            if st.session_state.main_menu == "📖 ノート操作" and st.session_state.mode == "ノート書き込みモード" and st.session_state.selected_note_id:
                selected_note = next((n for n in all_notes if n['id'] == st.session_state.selected_note_id), None)
                if selected_note:
                    is_recommended = selected_note['id'] == st.session_state.recommended_note_id

                    is_close_enough = False
                    if st.session_state.user_location:
                        user_coords = (st.session_state.user_location['latitude'], st.session_state.user_location['longitude'])
                        note_coords = (selected_note['lat'], selected_note['lng'])
                        if great_circle(user_coords, note_coords).km <= 10:
                                is_close_enough = True

                    if is_recommended or is_close_enough:
                        is_viewable = True
                    elif not st.session_state.user_location:
                        st.error("現在地が取得できていないため、ノートの内容を表示できません。")
                    else:
                        st.warning(f"このノートを閲覧・書き込みするには10km以内に近づく必要があります。")

            if is_viewable and selected_note:
                header_text = "👑 AIのおすすめ<br>" if selected_note['id'] == st.session_state.recommended_note_id else ""
                header_text += f"📖 {selected_note['title']}"
                st.markdown(header_text, unsafe_allow_html=True)

                if selected_note.get("hashtags"):
                    st.caption(" ".join(selected_note["hashtags"]))

                with st.container(height=300):
                    st.write("**これまでの書き込み**")
                    if not selected_note.get("entries", []):
                        st.info("まだ書き込みはありません。")

                    for entry in selected_note.get("entries", []):
                        st.markdown(f"**{entry['author_name']}** (`{datetime.fromtimestamp(entry['timestamp']).strftime('%Y-%m-%d %H:%M')}`)")
                        if entry.get("hashtags"):
                            st.caption(" ".join(entry["hashtags"]))
                        entry_type = entry.get('type')
                        if entry_type == 'text':
                            st.info(entry['data'])
                        elif entry_type in ['image', 'drawing']:
                            st.image(entry['data'], use_container_width=True)
                        elif entry_type == 'combined':
                            st.info(entry['text'])
                            st.image(entry['image'], use_container_width=True)
                        st.markdown("---")

                st.subheader("新しいページを追加")
                with st.form("entry_form", clear_on_submit=True):
                    text_input = st.text_area("メッセージ (任意)")
                    uploaded_file = st.file_uploader("画像を添付 (任意)", type=['png', 'jpg', 'jpeg'])
                    entry_hashtags = st.text_input("ハッシュタグ (スペース区切り)", placeholder="例: 楽しかった また来たい")
                    submitted = st.form_submit_button("投稿する")

                    if submitted:
                        post_allowed = False
                        if st.session_state.user_location and st.session_state.user_location.get('latitude'):
                            user_coords = (st.session_state.user_location['latitude'], st.session_state.user_location['longitude'])
                            note_coords = (selected_note['lat'], selected_note['lng'])
                            distance = great_circle(user_coords, note_coords).km
                            if distance <= 10:
                                post_allowed = True
                            else:
                                st.error(f"このノートには、10km以内に近づかないと書き込みできません。(現在約 {distance:.2f} km)")
                        else:
                            st.error("現在地が取得できていないため投稿できません。ブラウザで位置情報の使用を許可してください。")

                        if post_allowed:
                            if not text_input and not uploaded_file:
                                st.warning("メッセージを入力するか、画像をアップロードしてください。")
                            else:
                                new_entry = None
                                post_time = datetime.now().timestamp()
                                author_name = current_user_info['name']
                                hashtags = parse_hashtags(entry_hashtags)

                                if text_input and uploaded_file:
                                    img = Image.open(uploaded_file)
                                    b64_str = image_to_base64(img, format=img.format or "JPEG")
                                    new_entry = {"author_name": author_name, "timestamp": post_time, "type": "combined", "text": text_input, "image": b64_str, "hashtags": hashtags}
                                elif text_input:
                                    new_entry = {"author_name": author_name, "timestamp": post_time, "type": "text", "data": text_input, "hashtags": hashtags}
                                elif uploaded_file:
                                    img = Image.open(uploaded_file)
                                    b64_str = image_to_base64(img, format=img.format or "JPEG")
                                    new_entry = {"author_name": author_name, "timestamp": post_time, "type": "image", "data": b64_str, "hashtags": hashtags}

                                if new_entry:
                                    selected_note.setdefault("entries", []).append(new_entry)
                                    save_data(NOTES_FILE, all_notes)
                                    st.success("投稿しました！")
                                    st.rerun()

                if selected_note['creator_id'] == current_user_info['id']:
                    st.markdown("---")
                    with st.expander("🗑️ ノートを削除"):
                        st.warning("この操作は取り消せません。")
                        if st.checkbox("本当に削除しますか？"):
                            if st.button("このノートを削除する", type="primary"):
                                all_notes = [n for n in all_notes if n['id'] != st.session_state.selected_note_id]
                                save_data(NOTES_FILE, all_notes)
                                st.success(f"ノート「{selected_note['title']}」を削除しました。")
                                st.session_state.selected_note_id = None
                                st.session_state.nearby_notes = []
                                st.session_state.search_results = None
                                st.session_state.recommended_note_id = None
                                st.rerun()

            elif st.session_state.main_menu == "📖 ノート操作" and st.session_state.mode == "ノート書き込みモード" and not st.session_state.selected_note_id:
                st.info("サイドバーで書き込みたいノートを選択するか、AIにおすすめを聞いてみましょう。")

            elif st.session_state.main_menu != "📖 ノート操作":
                st.info("サイドバーで操作を選択してください。")

        if st.session_state.auto_refresh:
            time.sleep(5)
            st.rerun()
