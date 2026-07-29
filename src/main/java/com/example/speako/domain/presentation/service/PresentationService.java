package com.example.speako.domain.presentation.service;

import com.example.speako.domain.presentation.dto.PresentationRequestDTO;
import com.example.speako.domain.presentation.entity.Presentation;
import com.example.speako.domain.presentation.repository.PresentationRepository;
import com.example.speako.domain.user.entity.User;
import com.example.speako.domain.user.repository.UserRepository;
import org.springframework.transaction.annotation.Transactional;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class PresentationService {
    private final UserRepository userRepository;
    private final PresentationRepository presentationRepository;
    private final S3Service s3Service;

    @Transactional
    public void generatePresentationAndScript(MultipartFile file, PresentationRequestDTO.CreateDTO request, String email) {
        // 1. 유저 조회
        User user = userRepository.findByEmail(email)
                .orElseThrow(() -> new IllegalArgumentException("존재하지 않는 회원입니다."));

        // 2. 파일 유효성 검증 (확장자 및 용량 제한: 최대 20MB)
        if (file.isEmpty() || file.getOriginalFilename() == null) {
            throw new IllegalArgumentException("업로드된 파일이 비어있습니다.");
        }

        String originalFilename = file.getOriginalFilename();
        String extension = originalFilename.substring(originalFilename.lastIndexOf(".") + 1).toLowerCase();

        if (!extension.equals("ppt") && !extension.equals("pptx") && !extension.equals("pdf")) {
            throw new IllegalArgumentException("PPT, PPTX 또는 PDF 파일만 업로드 가능합니다.");
        }

        long maxFileSize = 20 * 1024 * 1024; // 20MB
        if (file.getSize() > maxFileSize) {
            throw new IllegalArgumentException("파일 용량은 최대 20MB를 초과할 수 없습니다.");
        }

        float fileSizeMB = (float) file.getSize() / (1024 * 1024);

        // 3. 파일 S3 업로드
        String fileUrl = s3Service.uploadFile(file);

        // 4. Presentations 엔티티 생성 및 DB 저장
        Presentation presentation = Presentation.builder()
                .user(user)
                .fileName(originalFilename)
                .fileType(extension.equals("pdf") ? Presentation.FileType.pdf : Presentation.FileType.ppt)
                .fileSize(fileSizeMB)
                .slideCount(0) // 파이썬 AI 서버에서 파싱 후 받아오거나 기본값 설정
                .topic(request.getTopic())
                .duration(request.getDuration())
                .tone(Presentation.Tone.valueOf(request.getTone()))
                .guideline(request.getGuideline())
                .fileUrl(fileUrl)
                .build();

        presentationRepository.save(presentation);

        // 5. 파이썬 AI 서버로 S3 파일 URL 또는 데이터 전송하여 대본 생성 요청 로직 (추후 연동)
        // callPythonAiServer(presentation.getPresentationId(), fileUrl, request);
    }
}