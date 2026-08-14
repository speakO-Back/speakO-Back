package com.example.speako.domain.evaluation.service;

import com.example.speako.domain.evaluation.dto.AiEvaluationResponseDto;
import com.example.speako.domain.evaluation.entity.Evaluation;
import com.example.speako.domain.evaluation.repository.EvaluationRepository;
import com.example.speako.domain.presentation.entity.Presentation; // 👈 Presentation 엔티티 임포트
import com.example.speako.domain.presentation.repository.PresentationRepository; // 👈 PresentationRepository 임포트
import com.example.speako.domain.recording.entity.VoiceRecording;
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

    // scriptId 대신 presentationId를 받도록 변경
    public Evaluation evaluateVoice(Long userId, Long presentationId, MultipartFile file, VoiceRecording savedRecording) {

        // 1. 발표 자료(Presentation)를 직접 조회하여 aiProjectId 획득
        Presentation presentation = presentationRepository.findById(presentationId)
                .orElseThrow(() -> new IllegalArgumentException("해당 발표 자료를 찾을 수 없습니다."));

        Long aiProjectId = presentation.getAiProjectId();
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

        // 5. DB 저장 (slideId 대신 presentationId 매핑)
        Evaluation evaluation = Evaluation.builder()
                .userId(userId)
                .presentationId(presentation.getPresentationId()) // 👈 slideId 대신 presentationId 저장
                .recordingId(savedRecording != null ? savedRecording.getRecordingId() : null)
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