# Google OAuth 로그인 설정 매뉴얼

영상공방(Odyssey VOD Studio)에 **"Google로 로그인"** 버튼을 켜는 방법입니다.
구글 클라우드 콘솔에서 OAuth 클라이언트를 발급받아 `.env`에 넣으면, 로그인 화면에
Google 버튼이 나타나고 본인 구글 계정으로 바로 로그인됩니다.

> 이 OAuth는 **앱 로그인 신원 확인**용입니다. NotebookLM 접속 인증(`nlm login`,
> 크롬 쿠키 방식)과는 별개 경로예요. 둘 다 같은 구글 계정을 쓰지만 메커니즘이 다릅니다.

---

## 0. 준비물

- 구글 계정 (예: `you@example.com`)
- 영상공방을 로컬에서 `http://127.0.0.1:7000` 으로 실행 중 (기본 포트 7000)

---

## 1. Google Cloud Console에서 OAuth 클라이언트 발급

### 1-1. 프로젝트 선택/생성
1. https://console.cloud.google.com 접속
2. 상단 프로젝트 선택 → **새 프로젝트**(또는 기존 프로젝트 사용)
   - 이름 예시: `odyssey-vodstudio`

### 1-2. OAuth 동의 화면(OAuth consent screen) 구성
1. 좌측 메뉴 **API 및 서비스 → OAuth 동의 화면**
2. User Type: **외부(External)** 선택 → 만들기
3. 앱 정보 입력
   - 앱 이름: `영상공방` (자유롭게)
   - 사용자 지원 이메일 / 개발자 연락처 이메일: 본인 이메일
4. **범위(Scopes)**: 별도 추가 불필요 — 우리 앱은 `openid`, `email`, `profile`만 사용 (기본 제공)
5. **테스트 사용자(Test users)**: 앱이 "테스트" 상태면 여기에 **로그인할 구글 계정을 반드시 추가**
   - 예: `you@example.com` 추가
   - (게시 상태로 올리지 않는 한, 등록된 테스트 사용자만 로그인 가능)
6. 저장

> 개인/소수 사용이면 "게시(Publish)"하지 않고 **테스트 상태 + 테스트 사용자 등록**으로
> 충분합니다.

### 1-3. 사용자 인증 정보(Credentials) 생성
1. 좌측 메뉴 **API 및 서비스 → 사용자 인증 정보**
2. 상단 **+ 사용자 인증 정보 만들기 → OAuth 클라이언트 ID**
3. 애플리케이션 유형: **웹 애플리케이션(Web application)**
4. 이름: `odyssey-vodstudio-web` (자유)
5. **승인된 리디렉션 URI(Authorized redirect URIs)** 에 아래를 **정확히** 추가:

   ```
   http://127.0.0.1:7000/api/auth/google/callback
   ```

   > ⚠️ 한 글자도 틀리면 안 됩니다. 끝에 슬래시(`/`)를 붙이지 마세요.
   > 포트를 7000이 아닌 다른 값으로 띄운다면 그 포트로 맞추고, 아래 `OAUTH_REDIRECT_BASE`도
   > 같은 값으로 설정하세요.
   > `localhost`와 `127.0.0.1`은 구글이 서로 다른 것으로 봅니다 — 브라우저로 접속하는 주소와
   > 동일하게 맞추세요. (둘 다 쓰고 싶으면 두 URI를 모두 등록)

6. **만들기** → 팝업에 표시되는 **클라이언트 ID**와 **클라이언트 보안 비밀(Client secret)** 복사

---

## 2. `.env` 설정

프로젝트 루트의 `.env` 파일(없으면 `.env.example`을 복사해 생성)에 아래를 추가합니다:

```dotenv
# Google OAuth — "Sign in with Google"
GOOGLE_OAUTH_CLIENT_ID=여기에_클라이언트_ID.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=여기에_클라이언트_보안비밀

# (선택) 공개 베이스 URL — 콜백 주소를 만드는 데 사용. 기본값 http://127.0.0.1:7000
# 브라우저로 접속하는 host:port 및 1-3의 리디렉션 URI와 반드시 일치시킬 것
# OAUTH_REDIRECT_BASE=http://127.0.0.1:7000

# (선택) 접근 제어 — 둘 중 하나라도 설정하면 매칭되는 이메일만 로그인 가능
# 가장 먼저 만들어지는 계정은 항상 관리자(admin)가 됩니다.
GOOGLE_OAUTH_ALLOWED_EMAILS=you@example.com
# GOOGLE_OAUTH_ALLOWED_DOMAIN=example.com
```

설정 키 설명:

| 키 | 필수 | 설명 |
|---|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` | ✅ | 1-3에서 발급한 클라이언트 ID. 이 값이 있어야 로그인 화면에 Google 버튼이 보임 |
| `GOOGLE_OAUTH_CLIENT_SECRET` | ✅ | 클라이언트 보안 비밀 |
| `OAUTH_REDIRECT_BASE` | ⬜ | 콜백 베이스 URL. 기본 `http://127.0.0.1:7000` |
| `GOOGLE_OAUTH_ALLOWED_EMAILS` | ⬜ | 허용 이메일 콤마 목록. 예: `a@example.com,b@example.com` |
| `GOOGLE_OAUTH_ALLOWED_DOMAIN` | ⬜ | 허용 도메인 1개. 예: `example.com` → 해당 도메인 이메일만 허용 |

> `.env` 변경 후에는 서버를 **재시작**해야 반영됩니다.

---

## 3. 동작 확인

1. 서버 실행 후 브라우저에서 `http://127.0.0.1:7000/login` 접속
2. 비밀번호 입력칸 아래에 **`or` 구분선 + "Sign in with Google" 버튼**이 보이면 정상
3. 버튼 클릭 → 구글 계정 선택/동의 → 자동으로 앱(`/`)으로 로그인됨
4. 처음 로그인하는 계정은 **관리자(admin)** 로 생성됩니다

명령줄에서도 빠르게 확인할 수 있습니다:

```bash
# google_oauth_enabled 가 true 면 설정 OK
curl -s http://127.0.0.1:7000/api/auth/status

# 302 + accounts.google.com 으로 리디렉션되면 OK
curl -s -i "http://127.0.0.1:7000/api/auth/google/login"
```

---

## 4. 동작 방식 (간단)

```
[로그인 화면]  --클릭-->  /api/auth/google/login
        |                         |  (state + PKCE 생성, 서버에 임시 저장)
        |                         v
        |                 accounts.google.com (구글 로그인/동의)
        |                         |
        v                         v
  앱 홈(/)  <--세션쿠키 발급--  /api/auth/google/callback
                              (code+state 검증 → 토큰 교환 → 이메일 확인
                               → 허용목록 체크 → 계정 찾기/생성 → 세션 쿠키)
```

- CSRF/탈취 방지를 위해 **PKCE(S256) + 1회용 state**를 사용합니다.
- 이메일은 구글의 `email_verified`가 참인 경우에만 통과합니다.
- OAuth로 만든 계정은 **비밀번호 로그인이 불가능**합니다(임의 해시 저장). 오직 구글 로그인으로만 들어옵니다.

관련 구현 파일:
- `src/google_oauth.py` — OAuth 플로우(PKCE, 토큰 교환, 이메일 검증, 허용목록)
- `routes/auth_routes.py` — `/api/auth/google/login`, `/api/auth/google/callback`
- `core/auth.py` — `create_oauth_user`, `create_session_for_user`
- `static/login.html` — Google 버튼
- `app.py` — 두 엔드포인트를 인증 예외(AUTH_EXEMPT)에 등록

---

## 5. 자주 나는 에러 (로그인 화면 상단에 표시됨)

| 표시 메시지 / `?error=` | 원인 | 해결 |
|---|---|---|
| `oauth_not_configured` | CLIENT_ID/SECRET 미설정 | `.env` 채우고 서버 재시작 |
| `redirect_uri_mismatch` (구글 화면) | 리디렉션 URI 불일치 | 1-5의 URI와 `OAUTH_REDIRECT_BASE`/접속 주소를 정확히 일치 |
| `oauth_not_allowed` | 허용목록/도메인에 없는 이메일 | `GOOGLE_OAUTH_ALLOWED_EMAILS`에 추가 |
| `oauth_no_account` | 계정 없음 + 자동생성 불가 | 첫 사용자거나 허용목록 매칭 시 자동생성됨. 관리자에게 계정 요청 또는 허용목록 설정 |
| `access_blocked` / 403 (구글) | 테스트 사용자 미등록 | 1-2의 **테스트 사용자**에 로그인 계정 추가 |
| `oauth_failed` | 토큰 교환/네트워크 오류 | 클라이언트 보안 비밀 재확인, 시간 동기화, 재시도 |

---

## 6. 보안 메모

- `.env`(특히 CLIENT_SECRET)는 **절대 깃에 커밋하지 마세요**. (`.gitignore`에 `.env` 포함 확인)
- 로컬 전용 사용을 권장합니다. 외부에 노출할 경우 HTTPS + 리버스 프록시를 쓰고
  `SECURE_COOKIES=true`, `OAUTH_REDIRECT_BASE`를 공개 도메인으로 설정하세요.
- 본인만 쓸 거라면 `GOOGLE_OAUTH_ALLOWED_EMAILS=you@example.com` 처럼
  **본인 이메일만 허용**해 두는 것이 가장 안전합니다.
