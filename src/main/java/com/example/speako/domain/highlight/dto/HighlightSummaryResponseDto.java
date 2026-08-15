package com.example.speako.domain.highlight.dto;

import lombok.Builder;
import lombok.Getter;
import java.util.List;
import java.util.Map;

@Getter
@Builder
public class HighlightSummaryResponseDto {
    private Long presentationId;
    private int totalCount;
    private Map<String, Long> categoryCounts; // 예: {"length": 3, "liaison": 5, "mismatch": 7}
    private List<HighlightSummaryItemDto> highlightItems;

    @Getter
    @Builder
    public static class HighlightSummaryItemDto {
        private Long highlightId;
        private String category;
        private String word;
        private String standardPronunciation; // 예: "구:성"
        private String ruleDesc;            // 예: "이 단어의 첫 음절은 길게 발음합니다."
        private int positionStart;
        private int positionEnd;
    }
}