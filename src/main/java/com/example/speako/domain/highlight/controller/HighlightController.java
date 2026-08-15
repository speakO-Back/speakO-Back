package com.example.speako.domain.highlight.controller;

import com.example.speako.domain.highlight.dto.HighlightResponseDto;
import com.example.speako.domain.highlight.dto.HighlightSummaryResponseDto;
import com.example.speako.domain.highlight.service.HighlightService;
import com.example.speako.global.common.ApiResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/presentations")
@RequiredArgsConstructor
public class HighlightController {

    private final HighlightService highlightService;

    // 예시 요청: GET /api/presentations/{presentationId}/highlights
    @GetMapping("/{presentationId}/highlights")
    public ResponseEntity<ApiResponse<HighlightResponseDto>> getPresentationHighlights(
            @PathVariable("presentationId") Long presentationId
    ) {
        try {
            HighlightResponseDto response = highlightService.getHighlightsForPresentation(presentationId);
            return ResponseEntity.ok(ApiResponse.onSuccess(response));
        } catch (Exception e) {
            return ResponseEntity.internalServerError()
                    .body(ApiResponse.onFailure("INTERNAL_SERVER_ERROR", e.getMessage()));
        }
    }
    //하이라이트 요약 및 단어 목록 조회
    @GetMapping("/{presentationId}/highlights/summary")
    public ResponseEntity<ApiResponse<HighlightSummaryResponseDto>> getHighlightSummary(
            @PathVariable("presentationId") Long presentationId
    ) {
        HighlightSummaryResponseDto response = highlightService.getHighlightSummary(presentationId);
        return ResponseEntity.ok(ApiResponse.onSuccess(response));
    }
}