package com.example.speako.domain.highlight.dto;

import lombok.Builder;
import lombok.Getter;
import java.util.List;

@Getter
@Builder
public class HighlightResponseDto {
    private Long presentationId;
    private List<ScriptHighlightGroupDto> scripts; // 슬라이드/스크립트별 대본과 하이라이트 목록

    @Getter
    @Builder
    public static class ScriptHighlightGroupDto {
        private Long scriptId;
        private Long slideId;
        private String content; // 해당 슬라이드 대본 텍스트
        private List<HighlightItemDto> highlights;
    }

    @Getter
    @Builder
    public static class HighlightItemDto {
        private Long highlightId;
        private String word;
        private String category;
        private String standardPronunciation;
        private String ruleDesc;
        private int positionStart;
        private int positionEnd;
    }
}