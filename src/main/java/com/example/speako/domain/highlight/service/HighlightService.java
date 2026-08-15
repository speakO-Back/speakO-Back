package com.example.speako.domain.highlight.service;

import com.example.speako.domain.highlight.dto.HighlightResponseDto;
import com.example.speako.domain.highlight.dto.HighlightSummaryResponseDto;
import com.example.speako.domain.highlight.entity.PronunciationHighlight;
import com.example.speako.domain.highlight.repository.PronunciationHighlightRepository;
import com.example.speako.domain.presentation.entity.Presentation;
import com.example.speako.domain.presentation.repository.PresentationRepository;
import com.example.speako.domain.script.entity.Script;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
public class HighlightService {

    private final PresentationRepository presentationRepository;
    private final PronunciationHighlightRepository highlightRepository;

    @Transactional(readOnly = true)
    public HighlightResponseDto getHighlightsForPresentation(Long presentationId) {
        // 1. 프레젠테이션 존재 확인
        Presentation presentation = presentationRepository.findById(presentationId)
                .orElseThrow(() -> new IllegalArgumentException("해당 프레젠테이션을 찾을 수 없습니다. presentationId: " + presentationId));

        // 2. 해당 프레젠테이션의 모든 하이라이트 데이터 한 번에 조회
        List<PronunciationHighlight> highlights = highlightRepository.findByPresentationId(presentationId);

        // 3. Script 기준으로 하이라이트 그룹화
        Map<Script, List<PronunciationHighlight>> highlightsByScript = highlights.stream()
                .collect(Collectors.groupingBy(PronunciationHighlight::getScript));

        // 4. DTO 구조로 변환
        List<HighlightResponseDto.ScriptHighlightGroupDto> scriptGroupDtos = highlightsByScript.entrySet().stream()
                .map(entry -> {
                    Script script = entry.getKey();
                    List<PronunciationHighlight> scriptHighlights = entry.getValue();

                    List<HighlightResponseDto.HighlightItemDto> itemDtos = scriptHighlights.stream()
                            .map(h -> HighlightResponseDto.HighlightItemDto.builder()
                                    .highlightId(h.getHighlightId())
                                    .word(h.getWord())
                                    .category(h.getCategory().getCode())
                                    .standardPronunciation(h.getStandardPronunciation())
                                    .ruleDesc(h.getRuleDesc())
                                    .positionStart(h.getPositionStart())
                                    .positionEnd(h.getPositionEnd())
                                    .build())
                            .collect(Collectors.toList());

                    return HighlightResponseDto.ScriptHighlightGroupDto.builder()
                            .scriptId(script.getScriptId())
                            .slideId(script.getSlide().getSlideId())
                            .content(script.getContent())
                            .highlights(itemDtos)
                            .build();
                })
                .collect(Collectors.toList());

        // 5. 최종 응답 조립
        return HighlightResponseDto.builder()
                .presentationId(presentation.getPresentationId())
                .scripts(scriptGroupDtos)
                .build();
    }
    /**
     * 발표 자료의 하이라이트 요약 및 카테고리별 단어 목록 조회 로직
     */
    @Transactional(readOnly = true)
    public HighlightSummaryResponseDto getHighlightSummary(Long presentationId) {
        // 1. 프레젠테이션 존재 확인
        Presentation presentation = presentationRepository.findById(presentationId)
                .orElseThrow(() -> new IllegalArgumentException("해당 프레젠테이션을 찾을 수 없습니다. presentationId: " + presentationId));

        // 2. 해당 프레젠테이션의 모든 하이라이트 데이터 조회
        List<PronunciationHighlight> highlights = highlightRepository.findByPresentationId(presentationId);

        // 3. 카테고리별 개수(Count) 집계
        Map<String, Long> categoryCounts = highlights.stream()
                .collect(Collectors.groupingBy(
                        h -> h.getCategory().getCode(),
                        Collectors.counting()
                ));

        // 4. 화면에 보여줄 개별 아이템 DTO 리스트로 변환
        List<HighlightSummaryResponseDto.HighlightSummaryItemDto> itemDtos = highlights.stream()
                .map(h -> HighlightSummaryResponseDto.HighlightSummaryItemDto.builder()
                        .highlightId(h.getHighlightId())
                        .category(h.getCategory().getCode())
                        .word(h.getWord())
                        .standardPronunciation(h.getStandardPronunciation()) // 예: "구:성"
                        .ruleDesc(h.getRuleDesc())                         // 예: "이 단어의 첫 음절은 길게 발음합니다."
                        .positionStart(h.getPositionStart())
                        .positionEnd(h.getPositionEnd())
                        .build())
                .collect(Collectors.toList());

        // 5. 최종 응답 조립
        return HighlightSummaryResponseDto.builder()
                .presentationId(presentation.getPresentationId())
                .totalCount(highlights.size())
                .categoryCounts(categoryCounts)
                .highlightItems(itemDtos)
                .build();
    }
}