package com.example.speako.domain.audio.service;

import com.example.speako.domain.audio.dto.TtsRequestDto;
import com.example.speako.domain.audio.dto.TtsResponseDto;
import com.example.speako.domain.highlight.entity.PronunciationHighlight;
import com.example.speako.domain.highlight.repository.PronunciationHighlightRepository;
import com.example.speako.domain.presentation.entity.Presentation;
import com.example.speako.domain.presentation.repository.PresentationRepository;
import com.example.speako.domain.script.entity.Script;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.client.HttpStatusCodeException;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.server.ResponseStatusException;

import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
public class AudioService {

    private final PresentationRepository presentationRepository;
    private final PronunciationHighlightRepository highlightRepository;

    private static final String AI_BASE_URL =
            System.getenv().getOrDefault("AI_BASE_URL",
                    "https://losing-charity-floyd-transparent.trycloudflare.com");

    private static void addApiKeyHeader(HttpHeaders headers) {
        String apiKey = System.getenv("SPEAKO_API_KEY");
        if (apiKey != null && !apiKey.isBlank()) {
            headers.set("X-API-Key", apiKey);
        }
    }

    /**
     * 1. 전체 대본 TTS 생성 로직
     */
    @Transactional(readOnly = true)
    public TtsResponseDto generateFullScriptAudio(TtsRequestDto.FullTtsDto request) {
        Presentation presentation = presentationRepository.findById(request.getPresentationId())
                .orElseThrow(() -> new IllegalArgumentException("존재하지 않는 발표 자료입니다."));

        // 슬라이드별 최신 대본을 모아서 하나의 긴 텍스트로 결합
        String combinedScript = presentation.getSlides().stream()
                .flatMap(slide -> slide.getScripts().stream())
                .collect(Collectors.groupingBy(Script::getSlide,
                        Collectors.maxBy(Comparator.comparing(Script::getVersion))))
                .values().stream()
                .filter(java.util.Optional::isPresent)
                .map(java.util.Optional::get)
                .sorted(Comparator.comparing(s -> s.getSlide().getSlideOrder()))
                .map(Script::getContent)
                .filter(content -> content != null && !content.isBlank())
                .collect(Collectors.joining("\n\n"));

        if (combinedScript.isBlank()) {
            throw new IllegalStateException("변환할 대본 내용이 존재하지 않습니다.");
        }

        // 파이썬 AI/TTS 서버로 텍스트와 옵션 전송 후 오디오 결과 수신
        return callAiTtsServer(combinedScript, request.getVoiceStyle(), request.getSpeed());
    }

    /**
     * 2. 특정 하이라이팅 단어 TTS 생성 로직
     */
    @Transactional(readOnly = true)
    public TtsResponseDto generateHighlightAudio(TtsRequestDto.HighlightTtsDto request) {
        PronunciationHighlight highlight = highlightRepository.findById(request.getHighlightId())
                .orElseThrow(() -> new IllegalArgumentException("존재하지 않는 하이라이트 정보입니다."));

        // 하이라이트된 단어 (필요하다면 앞뒤 문맥을 포함해서 텍스트 구성 가능)
        String targetText = highlight.getWord();
        if (targetText == null || targetText.isBlank()) {
            throw new IllegalStateException("음성으로 변환할 단어 텍스트가 없습니다.");
        }

        // 파이썬 AI/TTS 서버로 단어와 옵션 전송
        return callAiTtsServer(targetText, request.getVoiceStyle(), request.getSpeed());
    }

    /**
     * 공통: 파이썬 AI 서버의 TTS 엔드포인트 통신 메서드
     */
    private TtsResponseDto callAiTtsServer(String text, String voiceStyle, float speed) {
        RestTemplate restTemplate = new RestTemplate();
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(10_000);
        factory.setReadTimeout(60_000); // 음성 생성은 시간이 조금 걸릴 수 있으므로 60초 설정
        restTemplate.setRequestFactory(factory);

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        addApiKeyHeader(headers);

        Map<String, Object> body = new HashMap<>();
        body.put("text", text);
        body.put("voice_style", voiceStyle); // 예: "hyeri_energetic"
        body.put("speed", speed);             // 예: 1.2

        HttpEntity<Map<String, Object>> requestEntity = new HttpEntity<>(body, headers);

        try {
            // 파이썬 AI 서버의 TTS 라우트 (예시 경로: /api/tts/synthesize)
            ResponseEntity<Map> response = restTemplate.postForEntity(
                    AI_BASE_URL + "/api/tts/synthesize",
                    requestEntity,
                    Map.class
            );

            Map responseBody = response.getBody();
            if (responseBody == null || !responseBody.containsKey("audio_url")) {
                throw new IllegalStateException("AI 서버로부터 오디오 결과 주소를 받지 못했습니다.");
            }

            String audioUrl = String.valueOf(responseBody.get("audio_url"));
            Integer duration = responseBody.containsKey("duration") ?
                    Integer.parseInt(String.valueOf(responseBody.get("duration"))) : 0;

            return TtsResponseDto.builder()
                    .audioUrl(audioUrl)
                    .duration(duration)
                    .build();

        } catch (HttpStatusCodeException e) {
            throw new ResponseStatusException(e.getStatusCode(), "AI 음성 합성 실패: " + e.getResponseBodyAsString());
        }
    }
}