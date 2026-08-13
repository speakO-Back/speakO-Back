package com.example.speako.domain.evaluation.service;

import com.example.speako.domain.evaluation.dto.AiEvaluationResponseDto;
import com.example.speako.domain.evaluation.entity.Evaluation;
import com.example.speako.domain.evaluation.repository.EvaluationRepository;
import com.example.speako.domain.recording.entity.VoiceRecording; // 녹음 엔티티 임포트
import com.example.speako.domain.script.entity.Script;
import com.example.speako.domain.script.repository.ScriptRepository;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
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

@Service
@RequiredArgsConstructor
public class EvaluationService {

    private final ScriptRepository scriptRepository;
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

    // ⚠️ @Transactional을 일부러 뺐다 — AI 호출이 최대 600초라 트랜잭션 안에서 부르면
    //  DB 커넥션을 10분 붙잡는다. 연관 로딩은 fetch join 쿼리가 미리 끝내둔다.
    public Evaluation evaluateVoice(Long userId, Long scriptId, MultipartFile file, VoiceRecording savedRecording) {

        // 1. 대본 + 슬라이드 + 발표(aiProjectId)까지 한 번에 조회
        Script script = scriptRepository.findWithSlideAndPresentation(scriptId)
                .orElseThrow(() -> new IllegalArgumentException("해당 대본을 찾을 수 없습니다."));

        Long aiProjectId = script.getSlide().getPresentation().getAiProjectId();
        if (aiProjectId == null) {
            throw new IllegalStateException(
                    "이 발표에는 AI 프로젝트 번호가 없습니다. (aiProjectId 컬럼 도입 전에 만든 옛 데이터 — 발표 자료를 다시 업로드해주세요)");
        }

        // 2. 파일을 요청 스레드에서 즉시 byte[]로 확보 (톰캣 임시파일 소멸 대비)
        if (file.isEmpty()) {
            throw new IllegalArgumentException("업로드된 오디오 파일이 비어 있습니다.");
        }
        final byte[] fileBytes;
        try {
            fileBytes = file.getBytes();
        } catch (IOException e) {
            throw new RuntimeException("파일 변환 실패", e);
        }
        // AI 서버는 확장자로 형식을 검증한다(.m4a/.webm/.mp3/.wav)
        final String filename = file.getOriginalFilename() != null
                ? file.getOriginalFilename() : "audio.m4a";

        // 3. RestTemplate 준비 — 이 경로만 읽기 타임아웃 600초
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

        // 5. DB 저장 (VoiceRecording 연동 포함)
        Evaluation evaluation = Evaluation.builder()
                .userId(userId)
                .slideId(script.getSlide().getSlideId())
                .recordingId(savedRecording != null ? savedRecording.getRecordingId() : null) // 👈 객체가 아닌 ID(Long)를 넣습니다.
                .audioFileName(filename)
                .audioDuration(savedRecording != null ? savedRecording.getDuration() : 15)
                .totalScore(aiResult.getOverallScores().getPronunciationScore())
                .pronunciationScore(aiResult.getOverallScores().getAccuracy())
                .recognizedText(aiResult.getRecognizedText())
                .build();

        return evaluationRepository.save(evaluation);
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
}