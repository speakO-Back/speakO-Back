# EC2 배포 런북 — AI 서버 (speako-ai-server)

2026-08-12 작성. 스프링과 **같은 EC2**에 AI 서버(Docker)를 올리는 절차.
명령은 위에서 아래로 순서대로 실행한다. `${새IP}`는 인스턴스 재시작 후 받은 퍼블릭 IP.

## 0. 전제 조건 (스프링 팀 완료 확인)

- [ ] 인스턴스 유형 **t3.medium**(4GB)으로 변경 완료 (중지 → 유형 변경 → 시작)
- [ ] EBS 루트 볼륨 **20GB**로 확장 + 파티션 확장(`growpart`/`resize2fs`) 완료 — `df -h /`에서 Avail 13GB 안팎
- [ ] `.pem` 키 확보 (채팅·메신저 평문 전송 금지)
- [ ] 보안그룹: 22/8080만 개방, **8000은 개방하지 않음** (스프링이 localhost로 호출)

⚠️ 중지→시작으로 **퍼블릭 IP가 바뀐다.** 프론트가 스프링 주소를 IP로 들고 있으면 갱신할 것.

## 1. Docker 설치 (EC2에서, 최초 1회)

```bash
sudo apt-get update && sudo apt-get install -y docker.io
sudo usermod -aG docker ubuntu   # ubuntu 계정이 sudo 없이 docker 쓰게
exit                              # 재접속해야 그룹이 반영됨
```

## 2. 코드 받기 (EC2에서)

```bash
git clone https://github.com/chiung22/SpeaKO.git
cd SpeaKO/speako-ai-server
```

## 3. `.env` 준비

**(로컬 PC에서)** 로컬의 `.env`를 EC2로 복사한다 — 키 값이 채팅을 거치지 않는 유일한 경로:

```powershell
scp -i 키.pem c:/Users/송치웅/Desktop/Project/SpeaKO/speako-ai-server/.env ubuntu@${새IP}:~/SpeaKO/speako-ai-server/.env
```

**(EC2에서)** `SPEAKO_API_KEY`를 이 자리에서 생성해 채운다 (값이 머신 밖으로 안 나감).
복사해 온 `.env`에 플레이스홀더 줄이 이미 있으므로 **덧붙이지 말고 교체**한다
(덧붙이면 키가 두 줄이 되고, 어느 쪽이 이길지 도구마다 달라 디버깅 지옥이 된다):

```bash
cd ~/SpeaKO/speako-ai-server
sed -i "s|^SPEAKO_API_KEY=.*|SPEAKO_API_KEY=$(openssl rand -hex 32)|" .env
grep -c '^SPEAKO_API_KEY=' .env   # 1이어야 함
```

스프링도 같은 값이 필요하다. **같은 머신이므로** 스프링 담당자가 EC2 안에서 직접 읽어 가면 된다:
`grep SPEAKO_API_KEY ~/SpeaKO/speako-ai-server/.env`

## 4. 빌드 & 실행 (EC2에서)

```bash
cd ~/SpeaKO/speako-ai-server
docker build -t speako-ai .
docker run -d --name speako-ai --restart always \
  -p 127.0.0.1:8000:8000 \
  --env-file .env \
  -v /home/ubuntu/speako-data:/app/data \
  speako-ai
```

- `127.0.0.1:8000` 바인딩: 보안그룹 실수로 8000이 열려도 외부에서 접근 불가(이중 안전장치).
- `-v /home/ubuntu/speako-data`: SQLite·사용량 로그 보존. 컨테이너를 지워도 데이터가 남는다.

## 5. 헬스체크 (EC2에서)

```bash
curl -s localhost:8000/            # 서버 소개 JSON이 나와야 함
docker logs speako-ai | tail -20   # "SPEAKO_API_KEY가 비어 있습니다" 경고가 없어야 함
```

## 6. 스프링 쪽 수정 — 환경변수 2개만 설정

**EC2 대응 코드는 2026-08-12에 이미 전달·적용 완료** (`PresentationService`·`EvaluationService` 최종본):

- `AI_BASE_URL = System.getenv().getOrDefault("AI_BASE_URL", "<터널 주소>")` — 환경변수가
  없으면 터널(로컬 테스트), 있으면 그 값 사용
- 모든 AI 호출 헤더에 `X-API-Key` 자동 추가 (`SPEAKO_API_KEY` 환경변수가 있을 때만 —
  로컬에선 키가 꺼져 있어 헤더 없이도 통과, EC2에선 켜지므로 필수)
- 폴링 GET은 `getForEntity` → `exchange`로 전환돼 헤더가 실린다

따라서 EC2에서는 스프링 실행 전에 **환경변수 2개만** 설정하면 된다:

스프링 실행 환경변수 예 (systemd면 Environment=, 직접 실행이면 export):

```bash
export AI_BASE_URL=http://localhost:8000
export SPEAKO_API_KEY=<.env에서 읽은 값>
java -Xmx1g -jar app.jar   # 4GB 공유 머신이므로 JVM 상한 1GB 권장
```

## 7. E2E 검증 (배포 완료 판정)

1. 스프링 경유 대본 생성 1회 (202 → 폴링 → completed → DB 저장)
2. 스프링 경유 발음 평가 1회 — **이번엔 긴 녹음(1분 50초짜리)도 가능** (터널 120초 상한이 사라짐)
3. `docker logs speako-ai`에서 401/422/500 없는지 확인

## 7-1. 실제 프론트 연동 전 스프링 체크리스트 (2026-08-12 기준 미완)

로컬 테스트는 지름길(테스트 프로젝트 15번 고정)로 통과한 것이라, **실제 사용자 흐름은
아래를 끝내야 돌아간다.** EC2의 AI 서버는 빈 DB로 시작하므로 15번 프로젝트도 없다.

- [ ] **① 실제 업로드 흐름 연결 (최우선)** — `project_id=15L` 하드코딩 제거.
      사용자 파일로 `POST /api/projects`(multipart) → 응답의 `project_id`를 Presentation에
      저장(`aiProjectId` 컬럼 추가) → 그 값으로 `/api/script/full`·`/api/evaluation/audio` 호출.
      **안 하면 모든 사용자가 같은 대본을 받고, EC2에선 아예 404가 난다.**
      슬라이드 수는 응답 `data.slides` 배열 길이로 채운다.
- [ ] **`guideline` → `extra_requirement`** — AI 서버 필드명은 `extra_requirement`다.
      지금 보내는 `guideline` 키는 조용히 무시되고 있어서, 사용자가 적은 추가 요구사항이
      대본에 반영되지 않는다.
- [ ] **`audience`(발표 대상) 전달** — 피그마 '대상' 필드. 프론트가 보내면 그대로 body에 추가.
- [ ] **파일 검증에서 `.ppt` 제거** — AI 서버는 `.pptx`/`.pdf`만 받는다. 스프링이 `.ppt`를
      통과시키면 AI 서버 단계에서 415로 떨어진다. 프론트 안내 문구도 pptx/pdf로.
- [ ] **스프링 CORS에 프론트 도메인 허용** — 브라우저(`speakofront.vercel.app` 등)가
      스프링(8080)을 직접 부르므로, 스프링 쪽 CORS 설정에 프론트 도메인이 있어야 한다.
      (AI 서버 CORS는 무관 — 스프링 경유라 브라우저가 직접 안 부름)
- [ ] **프론트의 API 주소를 EC2 스프링 주소로** — IP 하드코딩이면 Elastic IP를 붙이거나
      재시작 때마다 갱신 필요.
- [ ] (권장) `generatePresentationAndScript`의 `@Transactional` 제거 — 폴링 90초 동안
      DB 커넥션을 점유한다. 동시 사용자 몇 명이면 커넥션 풀(기본 10개)이 마른다.

## 8. 운영 명령 모음

```bash
docker logs -f speako-ai                      # 실시간 로그
docker restart speako-ai                      # 재시작
# 코드 갱신 재배포:
cd ~/SpeaKO && git pull && cd speako-ai-server \
  && docker build -t speako-ai . \
  && docker rm -f speako-ai && docker run -d --name speako-ai --restart always \
     -p 127.0.0.1:8000:8000 --env-file .env -v /home/ubuntu/speako-data:/app/data speako-ai
```

## 9. 철거 (2026-08-22 이후)

시연이 끝나면 외부 API를 전부 해지하기로 확정(8/09). EC2도 같이 정리:
컨테이너/이미지 삭제 → 인스턴스 중지(요금 절약) 또는 종료 → `.env`의 키들은 해지로 무효화됨.
