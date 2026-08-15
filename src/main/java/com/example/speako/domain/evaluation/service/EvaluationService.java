package com.example.speako.domain.evaluation.service;

import com.example.speako.domain.evaluation.dto.AiEvaluationResponseDto;
import com.example.speako.domain.evaluation.dto.AiProjectResponseDto;
import com.example.speako.domain.evaluation.dto.EvaluationResponseDTO;
import com.example.speako.domain.evaluation.entity.Evaluation;
import com.example.speako.domain.evaluation.repository.EvaluationRepository;
import com.example.speako.domain.presentation.entity.Presentation; // 👈 Presentation 엔티티 임포트
import com.example.speako.domain.presentation.repository.PresentationRepository; // 👈 PresentationRepository 임포트
import com.example.speako.domain.recording.entity.VoiceRecording;
import com.example.speako.domain.user.entity.User;
import com.example.speako.domain.user.repository.UserRepository;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.transaction.Transactional;
import lombok.RequiredArgsConstructor;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Service;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.HttpStatusCodeException;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.server.ResponseStatusException;

import java.io.IOException;
import java.time.LocalDateTime;

@Service
@RequiredArgsConstructor
public class EvaluationService {

    private final UserRepository userRepository;
    private final PresentationRepository presentationRepository;
    private final EvaluationRepository evaluationRepository;
    private final ObjectMapper objectMapper = new ObjectMapper();

    private static final String AI_BASE_URL =
            System.getenv().getOrDefault("AI_BASE_URL",
                    "https://losing-charity-floyd-transparent.trycloudflare.com");

    private static void addApiKeyHeader(HttpHeaders headers) {
        String apiKey = System.getenv("SPEAKO_API_KEY");
        if (apiKey != null && !apiKey.isBlank()) {
            headers.set("X-API-Key", apiKey);
        }
    }

    // 반환 타입을 Evaluation 단독이 아닌, DTO를 포함하거나 DTO 자체를 반환하도록 변경할 수 있습니다.
    public EvaluationResponseDTO.ResultDTO evaluateVoice(Long userId, Long presentationId, MultipartFile file, VoiceRecording savedRecording) {

        // 1. 발표 자료(Presentation) 조회
        Presentation presentation = presentationRepository.findById(presentationId)
                .orElseThrow(() -> new IllegalArgumentException("해당 발표 자료를 찾을 수 없습니다."));

        Long aiProjectId = presentation.getAiProjectId();
        if (aiProjectId == null) {
            throw new IllegalStateException("이 발표에는 AI 프로젝트 번호가 없습니다.");
        }

        // 2. 파일 바이트 확보
        if (file.isEmpty()) {
            throw new IllegalArgumentException("업로드된 오디오 파일이 비어 있습니다.");
        }
        final byte[] fileBytes;
        try {
            fileBytes = file.getBytes();
        } catch (IOException e) {
            throw new RuntimeException("파일 변환 실패", e);
        }
        final String filename = file.getOriginalFilename() != null
                ? file.getOriginalFilename() : "audio.m4a";

        // 3. RestTemplate 설정 (타임아웃 600초)
        RestTemplate restTemplate = new RestTemplate();
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(10_000);
        factory.setReadTimeout(600_000);
        restTemplate.setRequestFactory(factory);

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.MULTIPART_FORM_DATA);
        addApiKeyHeader(headers);

        MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
        body.add("project_id", aiProjectId);
        body.add("audio_file", new ByteArrayResource(fileBytes) {
            @Override
            public String getFilename() {
                return filename;
            }
        });

        // 4. AI 서버 호출
        AiEvaluationResponseDto aiResult;
        try {
            ResponseEntity<AiEvaluationResponseDto> response = restTemplate.postForEntity(
                    AI_BASE_URL + "/api/evaluation/audio",
                    new HttpEntity<>(body, headers),
                    AiEvaluationResponseDto.class
            );
            aiResult = response.getBody();
        } catch (HttpStatusCodeException e) {
            throw new ResponseStatusException(
                    e.getStatusCode(), extractDetail(e.getResponseBodyAsString()));
        }

        if (aiResult == null || aiResult.getOverallScores() == null) {
            throw new IllegalStateException("AI 서버 응답이 비어 있습니다.");
        }

        Float pronunciationScore = aiResult.getOverallScores().getPronunciationScore();
        Float accuracyScore = aiResult.getOverallScores().getAccuracy();

        // 5. DB 저장용 Evaluation 엔티티 빌드 및 저장
        Evaluation evaluation = Evaluation.builder()
                .userId(userId)
                .presentationId(presentation.getPresentationId())
                .recordingId(savedRecording != null ? savedRecording.getRecordingId() : null)
                .audioFileName(filename)
                .audioDuration(savedRecording != null ? savedRecording.getDuration() : 15)
                .totalScore(pronunciationScore != null ? pronunciationScore : 0.0f)
                .pronunciationScore(accuracyScore != null ? accuracyScore : 0.0f)
                .recognizedText(aiResult.getRecognizedText())
                .build();

        Evaluation savedEvaluation = evaluationRepository.save(evaluation);

        // 6. 💡 프론트엔드가 하이라이팅을 그릴 수 있도록 ResultDTO로 조립해서 반환!
        return EvaluationResponseDTO.ResultDTO.builder()
                .evaluationId(savedEvaluation.getEvaluationId())
                .userId(userId)
                .slideId(presentation.getPresentationId()) // presentationId 매핑
                .recordingId(savedEvaluation.getRecordingId())
                .audioFileName(filename)
                .audioDuration(savedEvaluation.getAudioDuration())
                .totalScore(savedEvaluation.getTotalScore())
                .pronunciationScore(savedEvaluation.getPronunciationScore())
                .recognizedText(aiResult.getRecognizedText())
                .referenceText(aiResult.getReferenceText())     // 👈 원본 텍스트 전달
                .wordsDetail(aiResult.getWordsDetail())         // 👈 틀린 단어 및 span 상세 정보 전달 (하이라이팅용)
                // .feedbackDetail(...)                         // 필요한 경우 추가 피드백 매핑
                .evaluatedAt(LocalDateTime.now())
                .build();
    }

    /** FastAPI 에러 본문 {"detail": "..."}에서 사용자용 문구만 꺼낸다. */
    private String extractDetail(String responseBody) {
        try {
            JsonNode node = objectMapper.readTree(responseBody);
            if (node.has("detail")) {
                return node.get("detail").asText();
            }
        } catch (Exception ignored) {
        }
        return responseBody;
    }

    /**
     * AI 서버로 커스텀 대본(파일 또는 수기 텍스트)을 먼저 전송하고,
     * 파이썬 서버가 발급해 준 고유 프로젝트 번호(aiProjectId)를 받아옵니다.
     */
    private Long generateAiProjectIdForCustom(String scriptText, MultipartFile scriptFile) {
        RestTemplate restTemplate = new RestTemplate();
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(10_000);
        factory.setReadTimeout(30_000); // 대본 등록은 금방 끝나므로 30초면 충분합니다.
        restTemplate.setRequestFactory(factory);

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.MULTIPART_FORM_DATA);
        addApiKeyHeader(headers); // 기존에 만들어두신 API 키 헤더 추가 메서드 재사용

        MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();

        // Case 1: 파일이 업로드된 경우 -> 파일을 바이트로 변환해서 AI 서버로 전송
        if (scriptFile != null && !scriptFile.isEmpty()) {
            try {
                byte[] fileBytes = scriptFile.getBytes();
                String filename = scriptFile.getOriginalFilename() != null
                        ? scriptFile.getOriginalFilename() : "script.txt";

                body.add("file", new ByteArrayResource(fileBytes) {
                    @Override
                    public String getFilename() {
                        return filename;
                    }
                });
            } catch (IOException e) {
                throw new RuntimeException("대본 파일 읽기 실패", e);
            }
        }

        // Case 2: 수기 텍스트만 입력된 경우 -> 텍스트 데이터를 body에 담아서 전송
        else if (scriptText != null && !scriptText.isBlank()) {
            body.add("text", scriptText);
        } else {
            throw new IllegalArgumentException("대본 파일이나 텍스트 중 하나는 반드시 존재해야 합니다.");
        }

        // AI 서버의 대본 등록/프로젝트 생성용 엔드포인트 호출
        // (※ 파이썬 AI 서버 팀과 맞춘 실제 엔드포인트 주소로 변경 필요합니다. 예: /api/project/create 등)
        try {
            ResponseEntity<AiProjectResponseDto> response = restTemplate.postForEntity(
                    AI_BASE_URL + "/api/project/create-custom",
                    new HttpEntity<>(body, headers),
                    AiProjectResponseDto.class
            );

            AiProjectResponseDto result = response.getBody();
            if (result == null || result.getProjectId() == null) { // 👈 getProjectId()로 변경
                throw new IllegalStateException("AI 서버로부터 프로젝트 ID를 받아오지 못했습니다.");
            }

            return result.getProjectId(); // 👈 getProjectId()로 변경

        } catch (HttpStatusCodeException e) {
            throw new ResponseStatusException(
                    e.getStatusCode(), "AI 서버 프로젝트 생성 실패: " + extractDetail(e.getResponseBodyAsString()));
        }
    }
    /**
     * 파일이나 수기 텍스트로 대본을 등록할 때 Presentation 레코드를 새로 생성하는 메서드
     */
    @Transactional
    public Long createPresentationForCustomScript(Long userId, MultipartFile scriptFile, String scriptText) {
        String fileUrl = "";
        String fileName = "수기 작성 대본";
        Presentation.FileType fileType = Presentation.FileType.pdf; // 임시 기본 타입 (엔티티 Enum 기준)
        float fileSize = 0.0f;

        // 1. 대본 파일이 들어온 경우 (docx, pdf, txt)
        if (scriptFile != null && !scriptFile.isEmpty()) {
            validateScriptFile(scriptFile);
            fileName = scriptFile.getOriginalFilename();
            fileSize = (float) scriptFile.getSize();

            // 확장자에 따른 FileType 설정 (엔티티가 ppt, pdf만 지원한다면 확장자에 맞춰 분기)
            String ext = fileName.substring(fileName.lastIndexOf(".") + 1).toLowerCase();
            if (ext.equals("pdf")) {
                fileType = Presentation.FileType.pdf;
            } else {
                fileType = Presentation.FileType.ppt; // docx나 txt인 경우 적절히 매핑
            }

            // TODO: 실제 S3Service 주입받아서 업로드 구현 필요
            fileUrl = "https://s3.amazonaws.com/speako-presentations/" + java.util.UUID.randomUUID() + "_" + fileName;
        } else {
            // 텍스트만 온 경우를 위한 가짜 파일 URL 및 이름 처리 (nullable = false 방어)
            fileUrl = "https://s3.amazonaws.com/speako-presentations/text-script-" + java.util.UUID.randomUUID() + ".txt";
        }

        // 2. AI 서버에 대본/텍스트를 먼저 던져서 aiProjectId 받아오기
        Long aiProjectId = generateAiProjectIdForCustom(scriptText, scriptFile);

        // 3. User 엔티티 조회 (Presentation이 User 연관관계를 가지므로 필요)
        // 만약 userRepository가 없다면 주입받아야 합니다.
        User user = userRepository.findById(userId)
                .orElseThrow(() -> new IllegalArgumentException("존재하지 않는 유저입니다."));

        // 4. Presentations 테이블에 필수 컬럼을 모두 채워 메타데이터 저장
        Presentation presentation = Presentation.builder()
                .user(user)
                .fileName(fileName)
                .fileType(fileType)
                .fileSize(fileSize)
                .slideCount(1)             // 수기/파일 대본이므로 슬라이드 수는 기본 1개 처리
                .aiProjectId(aiProjectId)
                .topic("커스텀 대본 발표")     // 필수 컬럼 방어
                .duration(60)              // 기본 발표 시간 (예: 60초)
                .tone(Presentation.Tone.formal)
                .fileUrl(fileUrl)
                .build();

        Presentation savedPresentation = presentationRepository.save(presentation);
        return savedPresentation.getPresentationId();
    }
    private void validateScriptFile(MultipartFile file) {
        long maxSize = 20 * 1024 * 1024; // 20MB 제한
        if (file.getSize() > maxSize) {
            throw new IllegalArgumentException("대본 파일 크기는 최대 20MB까지 허용됩니다.");
        }
        String originalFilename = file.getOriginalFilename();
        if (originalFilename == null || !originalFilename.contains(".")) {
            throw new IllegalArgumentException("유효하지 않은 파일명입니다.");
        }

        String extension = originalFilename.substring(originalFilename.lastIndexOf(".") + 1).toLowerCase();
        if (!extension.equals("docx") && !extension.equals("txt") && !extension.equals("pdf")) {
            throw new IllegalArgumentException("지원하지 않는 파일 형식입니다. (docx, txt, pdf만 가능)");
        }
    }
}