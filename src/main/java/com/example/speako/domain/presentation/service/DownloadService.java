package com.example.speako.domain.presentation.service;

import com.example.speako.domain.highlight.entity.PronunciationHighlight;
import com.example.speako.domain.highlight.repository.PronunciationHighlightRepository;
import com.example.speako.domain.presentation.entity.Presentation;
import com.example.speako.domain.presentation.repository.PresentationRepository;
import com.example.speako.domain.script.entity.Script;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
public class DownloadService {

    private final PresentationRepository presentationRepository;
    private final PronunciationHighlightRepository highlightRepository;

    /**
     * 1. 순수 대본 텍스트 파일 생성 로직
     */
    @Transactional(readOnly = true)
    public ByteArrayInputStream generateScriptTextFile(Long presentationId) {
        Presentation presentation = presentationRepository.findById(presentationId)
                .orElseThrow(() -> new IllegalArgumentException("존재하지 않는 발표 자료입니다."));

        StringBuilder sb = new StringBuilder();
        sb.append("=== [ 발표 대본 ] ===\n\n");

        // 슬라이드 순서대로 최신 대본 내용 추출
        List<Script> scripts = getLatestScripts(presentation);
        for (Script script : scripts) {
            sb.append("[Slide ").append(script.getSlide().getSlideOrder()).append("]\n");
            sb.append(script.getContent() != null ? script.getContent() : "").append("\n\n");
        }

        return new ByteArrayInputStream(sb.toString().getBytes(StandardCharsets.UTF_8));
    }

    /**
     * 2. 하이라이팅 분석 내용이 포함된 대본/리포트 텍스트 파일 생성 로직
     */
    @Transactional(readOnly = true)
    public ByteArrayInputStream generateHighlightedScriptReport(Long presentationId) {
        Presentation presentation = presentationRepository.findById(presentationId)
                .orElseThrow(() -> new IllegalArgumentException("존재하지 않는 발표 자료입니다."));

        List<Script> scripts = getLatestScripts(presentation);
        List<PronunciationHighlight> highlights = highlightRepository.findByPresentationId(presentationId);

        // 스크립트별 하이라이트 매핑을 위한 준비
        Map<Long, List<PronunciationHighlight>> highlightsByScript = highlights.stream()
                .collect(Collectors.groupingBy(h -> h.getScript().getScriptId()));

        StringBuilder sb = new StringBuilder();
        sb.append("========================================\n");
        sb.append("      스피코(Speako) 발음 분석 리포트      \n");
        sb.append("========================================\n\n");

        for (Script script : scripts) {
            sb.append(String.format("--- [ Slide %d ] ---\n", script.getSlide().getSlideOrder()));
            sb.append("대본 내용:\n");
            sb.append(script.getContent()).append("\n\n");

            // 해당 스크립트에 속한 하이라이트 항목들이 있다면 하단에 정리해서 출력
            List<PronunciationHighlight> scriptHighlights = highlightsByScript.get(script.getScriptId());
            if (scriptHighlights != null && !scriptHighlights.isEmpty()) {
                sb.append("[ 발음 주의 단어 목록 ]\n");
                for (PronunciationHighlight h : scriptHighlights) {
                    sb.append(String.format(" • 단어: %s\n", h.getWord()));
                    sb.append(String.format("   - 표준 발음: %s\n", h.getStandardPronunciation()));
                    sb.append(String.format("   - 가이드: %s\n", h.getRuleDesc()));
                }
                sb.append("\n");
            }
            sb.append("----------------------------------------\n\n");
        }

        return new ByteArrayInputStream(sb.toString().getBytes(StandardCharsets.UTF_8));
    }

    /**
     * 슬라이드별 최신 버전의 대본 리스트를 순서대로 가져오는 헬퍼 메서드
     */
    private List<Script> getLatestScripts(Presentation presentation) {
        return presentation.getSlides().stream()
                .flatMap(slide -> slide.getScripts().stream())
                .collect(Collectors.groupingBy(Script::getSlide,
                        Collectors.maxBy(Comparator.comparing(Script::getVersion))))
                .values().stream()
                .filter(java.util.Optional::isPresent)
                .map(java.util.Optional::get)
                .sorted(Comparator.comparing(s -> s.getSlide().getSlideOrder()))
                .collect(Collectors.toList());
    }
}