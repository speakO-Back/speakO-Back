package com.example.speako.domain.presentation.controller;

import com.example.speako.domain.presentation.dto.PresentationRequestDTO;
import com.example.speako.domain.presentation.dto.PresentationResponseDTO;
import com.example.speako.domain.presentation.service.PresentationService;
import com.example.speako.global.common.ApiResponse;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.media.Content;
import lombok.RequiredArgsConstructor;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.util.Map;

@RestController
@RequestMapping("/api/presentations")
@RequiredArgsConstructor
public class PresentationController {

    private final PresentationService presentationService;

    // 1. 대본 최초 생성 및 업로드 (POST)
    @PostMapping(consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<ApiResponse<PresentationResponseDTO.DetailDTO>> createPresentation(
            @RequestPart("file") MultipartFile file,
            @RequestPart(value = "request", name = "request")
            @Parameter(content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE))
            PresentationRequestDTO.CreateDTO request,
            @AuthenticationPrincipal String email
    ) {
        Long presentationId = presentationService.generatePresentationAndScript(file, request, email);
        PresentationResponseDTO.DetailDTO response = presentationService.getPresentationDetails(presentationId);
        return ResponseEntity.ok(ApiResponse.onSuccess(response));
    }

    // 2. 특정 발표 자료 및 대본 조회 (GET)
    @GetMapping("/{presentationId}")
    public ResponseEntity<ApiResponse<PresentationResponseDTO.DetailDTO>> getPresentation(
            @PathVariable Long presentationId
    ) {
        PresentationResponseDTO.DetailDTO response = presentationService.getPresentationDetails(presentationId);
        return ResponseEntity.ok(ApiResponse.onSuccess(response));
    }

    // 3. 전체 재생성 컨트롤러 (유저가 수정한 대본/요청사항 반영)
    @PostMapping("/{presentationId}/regenerate")
    public ResponseEntity<ApiResponse<PresentationResponseDTO.DetailDTO>> regenerateAll(
            @PathVariable Long presentationId,
            @RequestBody(required = false) Map<String, Object> body) {

        Integer duration = body != null && body.get("duration") != null ? (Integer) body.get("duration") : null;
        String tone = body != null ? (String) body.get("tone") : null;
        String extra = body != null ? (String) body.get("extraRequirement") : null;
        String currentScript = body != null ? (String) body.get("currentScript") : null; // 👈 유저가 수정중이던 대본 추가

        return ResponseEntity.ok(ApiResponse.onSuccess(
                presentationService.regenerateAll(presentationId, duration, tone, extra, currentScript)));
    }

    // 4. 부분 재생성 컨트롤러 (유저가 수정한 대본/요청사항 반영)
    @PostMapping("/{presentationId}/scripts/{scriptId}/regenerate")
    public ResponseEntity<ApiResponse<PresentationResponseDTO.DetailDTO>> regenerateOne(
            @PathVariable Long presentationId,
            @PathVariable Long scriptId,
            @RequestBody(required = false) Map<String, Object> body) {

        String tone = body != null ? (String) body.get("tone") : null;
        String extra = body != null ? (String) body.get("extraRequirement") : null;
        String currentScript = body != null ? (String) body.get("currentScript") : null; // 👈 유저가 수정중이던 대본 추가

        return ResponseEntity.ok(ApiResponse.onSuccess(
                presentationService.regenerateOne(presentationId, scriptId, tone, extra, currentScript)));
    }

    //5.전체 대본 보기
    @GetMapping("/{presentationId}/full-script")
    public ResponseEntity<PresentationResponseDTO.FullScriptViewDTO> getFullScript(
            @PathVariable Long presentationId) {

        PresentationResponseDTO.FullScriptViewDTO response =
                presentationService.getFullScriptForRecording(presentationId);

        return ResponseEntity.ok(response);
    }
    // 6. 커스텀 대본 등록 (파일 또는 수기 텍스트 입력)
    @PostMapping(value = "/custom", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<ApiResponse<PresentationResponseDTO.DetailDTO>> createCustomPresentation(
            @RequestPart(value = "scriptFile", required = false) MultipartFile scriptFile,
            @RequestPart(value = "scriptText", required = false) String scriptText,
            @RequestParam(value = "topic", required = false, defaultValue = "커스텀 대본 발표") String topic,
            @AuthenticationPrincipal String email
    ) {
        // 서비스 단에서 이메일로 유저를 찾고, 파일/텍스트 처리 및 AI 프로젝트 생성을 수행하도록 구현
        Long presentationId = presentationService.createPresentationForCustomScript(email, scriptFile, scriptText, topic);
        PresentationResponseDTO.DetailDTO response = presentationService.getPresentationDetails(presentationId);

        return ResponseEntity.ok(ApiResponse.onSuccess(response));
    }
}