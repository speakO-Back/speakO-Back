package com.example.speako.domain.presentation.controller;

import com.example.speako.domain.presentation.dto.PresentationRequestDTO;
import com.example.speako.domain.presentation.service.PresentationService;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.media.Content;
import lombok.RequiredArgsConstructor;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

@RestController
@RequestMapping("/api/presentations")
@RequiredArgsConstructor
public class PresentationController {

    private final PresentationService presentationService;
    private final ObjectMapper objectMapper;

    @PostMapping(consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ResponseEntity<String> createPresentation(
            @RequestPart("file") MultipartFile file,
            @RequestPart(value = "request", name = "request")
            @Parameter(content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE))
            PresentationRequestDTO.CreateDTO request,
            @AuthenticationPrincipal String email
    ) {
        presentationService.generatePresentationAndScript(file, request, email);
        return ResponseEntity.ok("발표 자료 업로드 및 대본 생성 요청이 완료되었습니다.");
    }
}