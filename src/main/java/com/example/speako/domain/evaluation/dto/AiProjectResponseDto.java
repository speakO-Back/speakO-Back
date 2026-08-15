package com.example.speako.domain.evaluation.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Getter
@NoArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class AiProjectResponseDto {

    private boolean success;

    @JsonProperty("project_id") // 👈 최상위 snake_case 필드 매핑
    private Long projectId;

    private DataDto data;

    @Getter
    @NoArgsConstructor
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class DataDto {
        // 필요한 경우 내부 데이터 필드 정의
    }
}