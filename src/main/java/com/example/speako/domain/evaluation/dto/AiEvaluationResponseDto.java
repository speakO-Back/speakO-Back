package com.example.speako.domain.evaluation.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.util.List;

@Getter
@NoArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class AiEvaluationResponseDto {

    private boolean success;

    @JsonProperty("project_id")
    private Long projectId;

    /** 피드백 API(POST /api/evaluation/{id}/feedback) 부를 때 쓰는 값 */
    @JsonProperty("evaluation_id")
    private Long evaluationId;

    @JsonProperty("slide_number")
    private Integer slideNumber;

    @JsonProperty("overall_scores")
    private OverallScores overallScores;

    private Grades grades;

    @JsonProperty("reference_text")
    private String referenceText;

    @JsonProperty("recognized_text")
    private String recognizedText;

    @JsonProperty("words_detail")
    private List<WordDetail> wordsDetail;

    @Getter
    @NoArgsConstructor
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class OverallScores {
        private Float accuracy;       // 정확도 0~100
        private Float fluency;        // 유창성
        private Float completeness;   // 완성도

        /** 종합 발음 점수 — 화면의 "총점"은 이 값 */
        @JsonProperty("pronunciation_score")
        private Float pronunciationScore;
    }

    @Getter
    @NoArgsConstructor
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class Grades {   // "A"~"F"
        private String accuracy;
        private String fluency;
        private String completeness;

        @JsonProperty("pronunciation_score")
        private String pronunciationScore;
    }

    @Getter
    @NoArgsConstructor
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class WordDetail {
        private String word;

        @JsonProperty("accuracy_score")
        private Float accuracyScore;

        @JsonProperty("error_type")
        private String errorType;    // None / Mispronunciation / Omission

        @JsonProperty("reference_span")
        private List<Integer> referenceSpan;    // null 가능

        @JsonProperty("recognized_span")
        private List<Integer> recognizedSpan;   // null 가능
    }
}